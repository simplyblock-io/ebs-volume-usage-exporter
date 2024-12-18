# ebs-volume-usage-exporter
On average, 70% of cloud block storage such as EBS is under-utilized. That typically means that many of storage consumers (e.g. databases) request more storage that they need, creating overall storage waste. Simplyblock’s EBS Volume Usage Calculator helps you understand the usage of persistent storage inside an Amazon EKS cluster and identify opportunities for EBS cost optimization. This isn’t always easy since most volumes are dynamically provisioned, so there is no general overview of how much storage is being actually used.

## What is collected?

The ebs-volume-usage-exporter collects some basic volume data (e.g., aws region, provisioned capacity, IOPS, throughput, name, ...), as well as usage information (avg / max IOPS and throughput).

The following list has all exported CSV colums and their meaning:
- **aws_region:** The volume's AWS region (for multi region clusters)
- **pv_name:** The volume's UUID-based volume name
- **pv_size:** The volume's human readable provisioned capacity
- **ebs_volume_id:** The volume's Amazon EBS volume id
- **ebs_volume_type:** The volume's Amazon EBS volume type (one of sc1, st1, gp2, gp3, io1, io2)
- **ebs_size_gb:** The volume's provisioned capacity (as integer)
- **ebs_provisioned_iops:** The volume's provisioned IOPS (may be N/A in case of default)
- **ebs_provisioned_throughput:** The volume's provisioned throughput (may be N/A in case of default)
- **read_io_avg:** The volume's average read IOPS
- **read_io_max:** The volume's maximum read IOPS
- **write_io_avg:** The volume's average write IOPS
- **write_io_max:** The volume's maximum write IOPS
- **read_mbps_avg:** The volume's average read throughput
- **read_mbps_max:** The volume's maximum read throughput
- **write_mbps_avg:** The volume's average write throughput
- **write_mbps_max:** The volume's maximum write throughput
- **snapshots:** The volume's snapshot as a comma-separated list
- **gigabytes_available:** The volume's free disk space
- **start_time:** The start time of the measurement period
- **end_time:** The end time of the measurement period

A CSV file content may look like this:
```csv
aws_region,pv_name,pv_size,ebs_volume_id,ebs_volume_type,ebs_size_gb,ebs_provisioned_iops,ebs_provisioned_throughput,read_io_avg,read_io_max,write_io_avg,write_io_max,read_mbps_avg,read_mbps_max,write_mbps_avg,write_mbps_max,snapshots,gigabytes_available,start_time,end_time
us-east-2,pvc-5a6f744a-2bb0-4b89-889f-bb66bcbd1f63,150Gi,vol-0e41cb8b3856d5d12,st1,150,N/A,N/A,0.86,34.2,71.61,1164.2,0,0,0.06,0.94,,146.57,2024-12-08T14:12:48.590302+00:00,2024-12-09T14:12:48.590302+00:00
```


## Collector Usage

The collector is provided as a ready-to-use helm chart which uses the default python image to execute the collector script.

To deploy the helm chart, a few values need to be configured in the `values.yaml` file. Therefore, the following information need to be available:

  - Amazon EKS cluster with cluster admin privilege
  - S3 bucket

First, we need to create the necessary secret. To create it you need a `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. The following permissions are required:

- IAM EC2: Read-Only
- Cloudwatch: Read-Only
- S3 Bucket: Full

Use the following command to create the secret:

```bash
kubectl create secret generic aws-secret \
  --from-literal=AWS_ACCESS_KEY_ID=your-access-key-id \
  --from-literal=AWS_SECRET_ACCESS_KEY=your-secret-access-key
```

Afterward, fill in the name of the secret (in this case `aws-secret`) into the `values.yaml` under `AWS_SECRET_REF`.

Secondly, we need to create a service account, and cluster role (get and list PVs, PVCs, and snapshot). You can do this with the following commands:

```bash
kubectl create sa metric-aggregation-sa
kubectl apply -f manifests/storage-cluster-role.yaml
kubectl apply -f manifests/storage-cluster-role-binding.yaml
```

Finally, install he helm chart and let the tool run. After the successful export, you'll find the exported CSV file in your provided S3 bucket. To kick of the installation, use the following command. The `TIME_DURATION` parameter defines the number of days for the data collection.

```bash
helm install metric-agg charts/pv-metrics-aggregation/ \
  --set scriptConfig.S3_BUCKET_NAME=metrics-aggragation \
  --set scriptConfig.AWS_REGION=us-east-1 \
  --set scriptConfig.TIME_DURATION=1 \
  --set scriptConfig.CLUSTER_NAME=random
```
