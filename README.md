# pv-metrics-aggregation
script used to fetch persistent volume info and iostats

1) Get AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY with IAM EC2 and Cloudwatch Read-Only permission,use them to create a k8s secret:
`kubectl create secret generic aws-secret \
  --from-literal=AWS_ACCESS_KEY_ID=your-access-key-id \
  --from-literal=AWS_SECRET_ACCESS_KEY=your-secret-access-key`

2) put the name of secret 'aws-secret' in values file "AWS_SECRET_REF".

3) Create a serviceaccount :
`kubectl create sa metric-aggregation-sa`

3) Create a clusterrole to get,list pvs,pvcs,snapshot.
`kubectl apply -f manifests/storage-cluster-role.yaml`

4) Create a clusterrolebinding to serviceaccount.(replace the serviceaccount name acc to step 3)
`kubectl apply -f manifests/storage-cluster-role-binding.yaml`

5) deploy helm chart with:
    `helm install metric-agg charts/pv-metrics-aggregation/ --set scriptConfig.S3_BUCKET_NAME=metrics-aggragation --set scriptConfig.AWS_REGION=us-east-1 --set scriptConfig.TIME_DURATION=1`

TIME_DURATION is number of days in the past from now.
