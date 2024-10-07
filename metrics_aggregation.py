import csv
import time
import os
import argparse
import logging
import threading
import json
from kubernetes import client, config
import boto3
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

config.load_incluster_config()

v1 = client.CoreV1Api()

pv_info = []
write_lock = threading.Lock()

aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION', 'us-east-1')

cloudwatch = boto3.client(
    'cloudwatch',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=aws_region
)

s3_client = boto3.client(
    's3',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=aws_region
)

ec2_client = boto3.client(
    'ec2',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=aws_region
)

def get_persistent_volumes():
    log.info("Fetching Persistent Volumes information")
    global pv_info
    try:
        pvs = v1.list_persistent_volume().items
    except client.exceptions.ApiException as e:
        log.error(f"Error fetching PVs: {e}")
        return

    for pv in pvs:
        if pv.spec.aws_elastic_block_store:
            volume_id = pv.spec.aws_elastic_block_store.volume_id
        elif pv.spec.csi and pv.spec.csi.volume_handle:
            volume_id = pv.spec.csi.volume_handle
        else:
            log.warning(f"Volume ID not found for PV: {pv.metadata.name}. Skipping.")
            continue

        pv_info.append({
            "name": pv.metadata.name,
            "size": pv.spec.capacity['storage'],
            "claim_name": pv.spec.claim_ref.name if pv.spec.claim_ref else "N/A",
            "pv_ebs_volume_id": volume_id
        })
        log.info(f"Collected PV: {pv.metadata.name}, Volume ID: {volume_id}")
        
        
def get_volume_iops_limit(volume_id):
    # Retrieves the provisioned IOPS limit for the given EBS volume.
    try:
        response = ec2_client.describe_volumes(VolumeIds=[volume_id])
        if response['Volumes']:
            volume = response['Volumes'][0]
            return volume.get('Iops')
    except Exception as e:
        log.error(f"Failed to retrieve IOPS limit for volume {volume_id}: {e}")
        return None


def get_ebs_metrics(start_time, end_time, period=300):
    all_metrics = []
    for pv in pv_info:
        volume_id = pv['pv_ebs_volume_id']
        if not volume_id:
            log.error(f"Volume ID not found for PV: {pv['name']}")
            continue
        log.info(f"Fetching EBS metrics for volume: {volume_id}")
        metric_data = get_volume_metrics(volume_id, start_time, end_time, period)

        if not metric_data or not any(metric_data):
            log.warning(f"No metric data found for volume: {volume_id}. Skipping.")
            continue

        log.info(f"Received {len(metric_data)} data points")

        read_ops = metric_data[0]['Values'] 
        write_ops = metric_data[1]['Values']
        read_bytes = metric_data[2]['Values']
        write_bytes = metric_data[3]['Values']

        read_io_avg = round(sum(read_ops) / len(read_ops), 2) if read_ops else 0
        read_io_max = round(max(read_ops), 2) if read_ops else 0
        write_io_avg = round(sum(write_ops) / len(write_ops), 2) if write_ops else 0
        write_io_max = round(max(write_ops), 2) if write_ops else 0

        read_mbps_avg = round(sum(read_bytes)/len(read_bytes)/(1024*1024)/period,2) if read_bytes else 0
        read_mbps_max = round(max(read_bytes)/(1024*1024)/period, 2) if read_bytes else 0
        write_mbps_avg = round(sum(write_bytes)/len(write_bytes)/(1024*1024)/period,2) if write_bytes else 0
        write_mbps_max = round(max(write_bytes)/(1024*1024)/period, 2) if write_bytes else 0
        
        total_iops_per_second = read_io_avg/300 + write_io_avg/300
        provisioned_iops = get_volume_iops_limit(volume_id)
        
        percent_disk_utilized = round((total_iops_per_second / provisioned_iops) * 100, 2)



        snapshots = get_volume_snapshots(volume_id)
        snapshots_str = ', '.join(snapshots)

        metrics_entry = {
            'pv_name': pv['name'],
            'pv_size': pv['size'],
            'percent_disk_utilized': percent_disk_utilized,
            'ebs_volume_id': volume_id,
            'read_io_avg': read_io_avg,
            'read_io_max': read_io_max,
            'write_io_avg': write_io_avg,
            'write_io_max': write_io_max,
            'read_mbps_avg': read_mbps_avg,
            'read_mbps_max': read_mbps_max,
            'write_mbps_avg': write_mbps_avg,
            'write_mbps_max': write_mbps_max,
            'snapshots': snapshots_str,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat()  
        }
        all_metrics.append(metrics_entry)

        log.info(f"Metrics for PV {pv['name']} - Volume ID: {volume_id}, Read I/O Avg: {read_io_avg}, Read I/O Max: {read_io_max}, "
                 f"Write I/O Avg: {write_io_avg}, Write I/O Max: {write_io_max}, "
                 f"Read MB Avg: {read_mbps_avg} MB/s, Read MB Max: {read_mbps_max} MB/s, "
                 f"Write MB Avg: {write_mbps_avg} MB/s, Write MB Max: {write_mbps_max} MB/s, "
                 f"Snapshots: {snapshots_str}, Start Time: {start_time.isoformat()}, End Time: {end_time.isoformat()}")

        upload_raw_response_to_s3(metric_data, volume_id)

    return all_metrics

def get_volume_metrics(volume_id, start_time, end_time, period):
    response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                'Id': 'm1',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/EBS',
                        'MetricName': 'VolumeReadOps',
                        'Dimensions': [{'Name': 'VolumeId', 'Value': volume_id}]
                    },
                    'Period': period,
                    'Stat': 'Average',
                    'Unit': 'Count'
                },
                'ReturnData': True
            },
            {
                'Id': 'm2',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/EBS',
                        'MetricName': 'VolumeWriteOps',
                        'Dimensions': [{'Name': 'VolumeId', 'Value': volume_id}]
                    },
                    'Period': period,
                    'Stat': 'Average',
                    'Unit': 'Count'
                },
                'ReturnData': True
            },
            {
                'Id': 'm3',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/EBS',
                        'MetricName': 'VolumeReadBytes',
                        'Dimensions': [{'Name': 'VolumeId', 'Value': volume_id}]
                    },
                    'Period': period,
                    'Stat': 'Average',
                    'Unit': 'Bytes'
                },
                'ReturnData': True
            },
            {
                'Id': 'm4',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'AWS/EBS',
                        'MetricName': 'VolumeWriteBytes',
                        'Dimensions': [{'Name': 'VolumeId', 'Value': volume_id}]
                    },
                    'Period': period,
                    'Stat': 'Average',
                    'Unit': 'Bytes'
                },
                'ReturnData': True
            },
        ],
        StartTime=start_time,
        EndTime=end_time,
        ScanBy='TimestampDescending'
    )
    return response['MetricDataResults']

def get_volume_snapshots(volume_id):
    snapshots = []
    try:
        response = ec2_client.describe_snapshots(
            Filters=[{'Name': 'volume-id', 'Values': [volume_id]}],
            OwnerIds=['self']
        )
        for snapshot in response['Snapshots']:
            snapshots.append(snapshot['SnapshotId'])
    except Exception as e:
        log.error(f"Failed to retrieve snapshots for volume {volume_id}: {e}")

    return snapshots

def upload_raw_response_to_s3(response, volume_id):
    response_file = f'response_{volume_id}.json'
    try:
        with open(response_file, 'w') as f:
            json.dump(response, f, indent=4, default=str)
        s3_client.upload_file(response_file, os.getenv("S3_BUCKET_NAME"), f'response/{response_file}')
        log.info(f"Uploaded raw response to S3 as {response_file}")
        os.remove(response_file)
    except Exception as e:
        log.error(f"Failed to upload raw response for volume {volume_id}: {e}")




def write_metrics_to_s3(metric_data, bucket_name):
    if not metric_data:
        log.warning("No metric data to write to S3.")
        return

    temp_file = 'temp_metrics.csv'
    with open(temp_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=metric_data[0].keys())
        writer.writeheader()
        writer.writerows(metric_data)

    log.info(f"Metrics written to temporary file {temp_file}")

    upload_to_s3(temp_file, bucket_name, 'datafile.csv')

    os.remove(temp_file)
    
    


def upload_to_s3(file_name, bucket, object_name=None):
    if object_name is None:
        object_name = file_name

    try:
        s3_client.upload_file(file_name, bucket, object_name)
        log.info(f"Uploaded {file_name} to S3 bucket {bucket}/{object_name}")
    except Exception as e:
        log.error(f"Failed to upload {file_name} to S3: {e}")

def main():
    parser = argparse.ArgumentParser(description="Collect pv iostats data.")
    
    time_duration = os.getenv("TIME_DURATION")
    
    if not time_duration:
        log.error("TIME_DURATION environment variable is not set.")
        return

    try:
        time_duration = int(time_duration)
    except ValueError:
        log.error("Invalid TIME_DURATION environment variable. Must be an integer.")
        return

    bucket_name = os.getenv("S3_BUCKET_NAME")
    if not bucket_name:
        log.error("S3_BUCKET_NAME environment variable is not set.")
        return

    log.info(f"Using S3 bucket: {bucket_name}")

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=time_duration)

    log.info(f"Script started with arguments - Time: {time_duration}s, Bucket: {bucket_name}, Start Time: {start_time.isoformat()}, End Time: {end_time.isoformat()}")

    get_persistent_volumes()

    if not pv_info:
        log.error("No PV information available. Exiting.")
        return

    metrics_data = get_ebs_metrics(start_time, end_time)
    write_metrics_to_s3(metrics_data, bucket_name)

    log.info("Script execution completed.")

if __name__ == "__main__":
    main()
