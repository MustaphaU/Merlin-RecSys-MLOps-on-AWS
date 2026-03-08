# Deploying a Recommender System with Continuous Retraining on Amazon EKS
*Autoscaling with Kubernetes Cluster Autoscaler and Kubernetes Horizontal Pod Autoscaler*


## Follow these steps to set up the infrastructure and deploy the recommender system:


### 1. Export env variables
```bash
export REGION=us-east-1
export CLUSTER=my-merlin-cluster
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

### 2. Create the cluster (adjust desired capacity) 
* create cluster-config.yaml file.
    ```yaml
    apiVersion: eksctl.io/v1alpha5
    kind: ClusterConfig
    metadata:
    name: my-merlin-cluster
    region: us-east-1
    version: "1.34"

    iam: 
    withOIDC: true
    managedNodeGroups:
    - name: gpu-node-group
        instanceType: g5.xlarge
        amiFamily: Ubuntu2404
        minSize: 1
        desiredCapacity: 1
        maxSize: 3
        volumeSize: 100
        taints:
        - key: nvidia.com/gpu
            value: "present"
            effect: NoSchedule
        labels:
        hardware-type: gpu
        nodegroup: gpu-node-group
        ssh:
        allow: true
        publicKeyPath: ~/.ssh/id_rsa.pub
        tags:
        k8s.io/cluster-autoscaler/enabled: "true"
        k8s.io/cluster-autoscaler/my-merlin-cluster: "owned"
    
    - name: cpu-node-group
        instanceType: m5.xlarge
        amiFamily: Ubuntu2404
        minSize: 1
        desiredCapacity: 1
        maxSize: 3
        volumeSize: 50
        labels:
        hardware-type: cpu
        nodegroup: cpu-node-group
        ssh:
        allow: true
        publicKeyPath: ~/.ssh/id_rsa.pub
        tags:
        k8s.io/cluster-autoscaler/enabled: "true"
        k8s.io/cluster-autoscaler/my-merlin-cluster: "owned"
    ```

* create the cluster
    ```bash
    eksctl create cluster -f cluster-config.yaml
    ```
* Update kubeconfig
    ```bash
    aws eks update-kubeconfig --region $REGION --name $CLUSTER
    ```

### 3 Install NVIDIA GPU Operator
* Install the helm cli:
    ```bash
    curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 \
    && chmod 700 get_helm.sh \
    && ./get_helm.sh
    ```

* Create a gpu-operator namespace and set the enforcement policy to previleged
    ```bash
    kubectl create ns gpu-operator
    kubectl label --overwrite ns gpu-operator pod-security.kubernetes.io/enforce=privileged
    ```

* Add the NVIDIA Helm repository
    ```bash
    helm repo add nvidia https://helm.ngc.nvidia.com/nvidia \
        && helm repo update
    ```

* Install the GPU Operator (I have chosen driver 570.XX/ cuda 12.x)
    ```bash
    helm install --wait --generate-name \
    -n gpu-operator --create-namespace \
    nvidia/gpu-operator \
    --version=v25.10.1 \
    --set driver.version=570.195.03
    ```

* To confirm driver installation, create a pod and run `nvidia-smi` inside the pod (a container):  
    i. create cuda-vectoradd.yaml  

    ```bash
    apiVersion: v1
    kind: Pod
    metadata:
    name: nvidia-smi-pod
    spec:
    restartPolicy: Never
    containers:
    - name: nvidia-smi
        image: nvidia/cuda:12.2.0-base-ubuntu22.04
        command: ["nvidia-smi"]
        resources:
        limits:
            nvidia.com/gpu: 1
    ```
    ii. run `kubectl apply -f cuda-vectoradd.yaml`  
    iii. check logs: `kubectl logs nvidia-smi-pod`  
    ```
    +-----------------------------------------------------------------------------------------+
    | NVIDIA-SMI 570.195.03             Driver Version: 570.195.03     CUDA Version: 12.8     |
    |-----------------------------------------+------------------------+----------------------+
    | GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
    | Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
    |                                         |                        |               MIG M. |
    |=========================================+========================+======================|
    |   0  NVIDIA A10G                    On  |   00000000:00:1E.0 Off |                    0 |
    |  0%   22C    P8             23W /  300W |       0MiB /  23028MiB |      0%      Default |
    |                                         |                        |                  N/A |
    +-----------------------------------------+------------------------+----------------------+
                                                                                            
    +-----------------------------------------------------------------------------------------+
    | Processes:                                                                              |
    |  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
    |        ID   ID                                                               Usage      |
    |=========================================================================================|
    |  No running processes found                                                             |
    +-----------------------------------------------------------------------------------------+
    ```
    iv. delete pod: `kubectl delete -f cuda-vectoradd.yaml`


### 4. Add the EFS CSI Driver
* Find the EFS CSI driver version compatible with your platform version
```bash
aws eks describe-addon-versions --addon-name aws-efs-csi-driver
```
I chose `v2.3.0-eksbuild.1` for my platform version 1.34

* create IAM role and attach this policy `AmazonEFSCSIDriverPolicy`
    ```bash
    export CLUSTER=my-merlin-cluster
    export role_name=AmazonEKS_EFS_CSI_DriverRole
    eksctl create iamserviceaccount \
        --name efs-csi-controller-sa \
        --namespace kube-system \
        --cluster $CLUSTER \
        --role-name $role_name \
        --role-only \
        --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEFSCSIDriverPolicy \
        --approve
    TRUST_POLICY=$(aws iam get-role --output json --role-name $role_name --query 'Role.AssumeRolePolicyDocument' | \
        sed -e 's/efs-csi-controller-sa/efs-csi-*/' -e 's/StringEquals/StringLike/')
    aws iam update-assume-role-policy --role-name $role_name --policy-document "$TRUST_POLICY"
    ```
* create EFS CSI addon
    ```bash
    eksctl create addon --cluster $CLUSTER --name aws-efs-csi-driver --version v2.3.0-eksbuild.1 \
    --service-account-role-arn arn:aws:iam::${AWS_ACCOUNT_ID}:role/${role_name} --force
    ```

### 5. [Install EBS CSI driver](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)

* Find the driver version compatible with your platform version
    ```bash
    aws eks describe-addon-versions --addon-name aws-ebs-csi-driver
    ```
    v1.55.0-eksbuild.1 works with our platform (kubernetes) version 1.34

* create Amazon EBS CSI driver IAM role for service account and attach `AmazonEBSCSIDriverPolicy`
    ```bash
    eksctl create iamserviceaccount \
    --name ebs-csi-controller-sa \
    --namespace kube-system \
    --cluster $CLUSTER \
    --role-name AmazonEKS_EBS_CSI_DriverRole \
    --role-only \
    --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
    --approve
    ```
* create the EBS CSI addon
    ```bash
    eksctl create addon --cluster $CLUSTER --name aws-ebs-csi-driver --version v1.55.0-eksbuild.1 \
    --service-account-role-arn arn:aws:iam::${AWS_ACCOUNT_ID}:role/AmazonEKS_EBS_CSI_DriverRole --force
    ```

* set default StorageClass: I chose gp2
```bash
kubectl patch storageclass gp2 -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```
Why EBS: Some core Kubeflow components need **exclusive** *not* shared storage  (e.g. databases and metadata). EBS is preferred for single pod access.

### 6. [Install Kubeflow Pipelines (Standalone deployment *not* Full)](https://docs.aws.amazon.com/sagemaker/latest/dg/kubernetes-sagemaker-components-install.html#kubeflow-pipelines-standalone)  

I skipped the step: *creating a gateway node* because I have a machine that can:  

```
* Call AWS APIs (EKS, IAM, EC2, CloudFormation, S3)

* Talk to the Kubernetes API server

* Authenticate to EKS
```
Also skipped: *Set up an Amazon EKS cluster* (there is an existing cluster)  

i.  [Install the Kubeflow Pipelines.](https://www.kubeflow.org/docs/components/pipelines/operator-guides/installation/)
```bash
export PIPELINE_VERSION=2.16.0
kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/cluster-scoped-resources?ref=$PIPELINE_VERSION"
kubectl wait --for condition=established --timeout=60s crd/applications.app.k8s.io
kubectl apply -k "github.com/kubeflow/pipelines/manifests/kustomize/env/dev?ref=$PIPELINE_VERSION"                             
```

**TAKES APPROX. 3 minutes to complete**

ii. access the Kubeflow pipelines UI  
- port forward the Kubeflow Pipelines UI  
    ```
    kubectl port-forward -n kubeflow svc/ml-pipeline-ui 8080:80
    ```
- Open http://localhost:8080 on your browser to access the Kubeflow Pipelines UI.


### 7. [Create the network file system: EFS](https://github.com/kubernetes-sigs/aws-efs-csi-driver/blob/master/docs/efs-create-filesystem.md)
a. Where is the cluster?

* `VPC_ID`: Get the virtual network the cluster lives in.
    ```bash
    VPC_ID=$(aws eks describe-cluster --name $CLUSTER --region $REGION \
                --query "cluster.resourcesVpcConfig.vpcId" \
                --output text)
    ```

* `CIDR`: Retrieve the CIDR range for your cluster's VPC: I picked the first entry of Vpcs (ie. Vpcs[0]) since Vpcs is a list (of one CIDR).
    ```bash
    cidr_range=$(aws ec2 describe-vpcs \
    --vpc-ids $VPC_ID \
    --query "Vpcs[0].CidrBlock" \
    --output text \
    --region $REGION)
    ```

b. Create a security group with an inbound rule that allows inbound NFS traffic for your Amazon EFS mount points.  
* create a security group
    ```bash
    security_group_id=$(aws ec2 create-security-group \
    --group-name MyEfsSecurityGroup \
    --description "My EFS security group" \
    --vpc-id $VPC_ID \
    --query 'GroupId' \
    --output text)
    ```
* create an inbound rule that allows inbound NFS traffic from the CIDR for your cluster's VPC.  
    ```bash
    aws ec2 authorize-security-group-ingress \
    --group-id $security_group_id \
    --protocol tcp \
    --port 2049 \
    --cidr $cidr_range
    ```

c. create an EFS for the EKS cluster  
* create a file system
    ```bash
    file_system_id=$(aws efs create-file-system \
    --region $REGION \
    --performance-mode generalPurpose \
    --query 'FileSystemId' \
    --output text)
    ```
* create mount targets  
    a. Determine the IP (INTERNAL-IP) addresses of your cluster nodes.
    ```
    kubectl get nodes -o wide
    ```

    b. Determine the IDs of the subnets in your (cluster) VPC and which Availability Zone the subnet is in.  
    ```bash
    aws ec2 describe-subnets \
        --filters "Name=vpc-id,Values=$VPC_ID" \
        --query 'Subnets[*].{SubnetId: SubnetId,AvailabilityZone: AvailabilityZone,CidrBlock: CidrBlock}' \
        --output table
    ```

    c. fetch the cluster's nodegroups  
    ```bash
    NODEGROUPS=$(aws eks list-nodegroups --cluster-name $CLUSTER --region $REGION --query "nodegroups[]" --output text)
    ```

    d. fetch the nodegroups' subnets
    ```bash
    SUBNETS=$(for ng in $NODEGROUPS; do
    aws eks describe-nodegroup --cluster-name $CLUSTER --nodegroup-name $ng --region $REGION --query "nodegroup.subnets[]" --output text 
    done | tr '\t' '\n' | sort -u)
    ```
    The script reads the unique nodegroup subnets (sort -u) for all the nodegroups in our cluster.

    e. create mount targets for the nodegroup subnets
    ```bash
    for sn in $SUBNETS; do
    aws efs create-mount-target --file-system-id $file_system_id --subnet-id $sn --security-groups $security_group_id --region $REGION >/dev/null
    done
    ```
    We can only create one mount target for each AZ; since our cluster is using managed nodegroups, we know that each nodegroup has at most one subnet in an AZ. So we can reliably create mount targets using the unique subnets without accidently attempting to create two mount targets in the same AZ:

    f. optional: confirm mount targets created.
    ```bash
    aws efs describe-mount-targets --file-system-id $file_system_id --region $REGION \
    --query 'MountTargets[].{Subnet:SubnetId,AZ:AvailabilityZoneId,State:LifeCycleState,IP:IpAddress}' --output table
    ```
    probable ouptut:
    ```bash
    -------------------------------------------------------------------------
    |                         DescribeMountTargets                          |
    +----------+------------------+------------+----------------------------+
    |    AZ    |       IP         |   State    |          Subnet            |
    +----------+------------------+------------+----------------------------+
    |  use1-az2|  192.168.xx.xyy  |  available |  subnet-EXAMPLE251d10315f  |
    |  use1-az5|  192.168.xx.xyz  |  available |  subnet-EXAMPLEb59403df0b  |
    +----------+------------------+------------+----------------------------+
    ```

### 8. Create the storage class and persistent volume claim. CSI dynamic provisioning creates PV automatically (reason for efs-ap provisioning mode).  
* create yaml file: *efs-storage.yaml*
    ```yaml
    apiVersion: storage.k8s.io/v1
    kind: StorageClass
    metadata:
    name: efs-sc
    provisioner: efs.csi.aws.com
    parameters:
    provisioningMode: efs-ap         # dynamic access points
    fileSystemId: FSID_REPLACE_ME #you may replace to make less permissive
    directoryPerms: "777"
    mountOptions:
    - tls
    ---
    apiVersion: v1
    kind: PersistentVolumeClaim
    metadata:
    name: merlin-pvc
    spec:
    accessModes: [ReadWriteMany]
    storageClassName: efs-sc
    resources:
        requests:
        storage: 100Gi
    ```
    Note: `resources.capacity` is actually ignored by Amazon EFS CSI driver when provisioning the volume claim because Amazon EFS is an elastic file system. Only specified because it is a required field in Kubernetes. Point is, the value doesn't limit the size of your Amazon EFS file system.

* inject file system ID into the yaml and apply the configuration. Ensure to create the PVC in kubeflow namespace so kfp pipeline can access it.
    ```bash
    sed "s/FSID_REPLACE_ME/$file_system_id/g" efs-storage.yaml | kubectl apply -n kubeflow -f -
    ```

* optional: confirm sc and pvc created
    ```bash
    kubectl get sc,pvc -n kubeflow
    ```

* bonus: test the persistent volume claim  
    i. create a pod named *`efs-test`*   
    (PS: YOU can test many pods on the volume)
    ```bash
    kubectl run efs-test --image=busybox --restart=Never --overrides='{"spec":{"containers":[{"name":"efs-test","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"efs-vol","mountPath":"/var/lib/data"}]}],"volumes":[{"name":"efs-vol","persistentVolumeClaim":{"claimName":"merlin-pvc"}}]}}' -n kubeflow
    ```
    creates a pod with `busybox` image, with volume mount at `"/var/lib/data/"` 

    ii. exec into the pod
    ```bash
    kubectl exec -it efs-test -n kubeflow -- sh
    ```
    iii. Once inside the shell environment, test the EFS mount like so:
    ```bash
    ls /var/lib/data && mkdir -p /var/lib/data/criteo-data && echo "EFS test successful" > /var/lib/data/criteo-data/test.txt && cat /var/lib/data/criteo-data/test.txt
    ```
    iv. clean up
    ```bash
    kubectl delete pod efs-test -n kubeflow
    ```

### 9. Create a Service Account and RBAC Role for Pipeline Components to Access Helm and Deploy pods, etc.
The data extraction and preprocess-train components need permission to check Helm release status for Triton (`triton_status=$(helm status triton 2>&1)`). Create a service account with minimal permissions.
```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: merlin-sa
  namespace: kubeflow
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: merlin-pipeline-role
  namespace: kubeflow
rules:
# For helm status and install (secrets store helm releases)
- apiGroups: [""]
  resources: ["secrets", "configmaps"]
  verbs: ["get", "list", "create", "update", "patch", "delete"]
# For deploying Triton
- apiGroups: [""]
  resources: ["pods", "pods/log", "services"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
# For argo workflows
- apiGroups: ["argoproj.io"]
  resources: ["workflows", "workflowtaskresults"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
- apiGroups: ["monitoring.coreos.com"]
  resources: ["servicemonitors"]
  verbs: ["get", "list", "create", "update", "patch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: merlin-pipeline-binding
  namespace: kubeflow
subjects:
- kind: ServiceAccount
  name: merlin-sa
  namespace: kubeflow
roleRef:
  kind: Role
  name: merlin-pipeline-role
  apiGroup: rbac.authorization.k8s.io
EOF
```
Verify service account
```bash
kubectl get serviceaccount merlin-sa -n kubeflow
kubectl get role merlin-pipeline-role -n kubeflow
kubectl get rolebinding merlin-pipeline-binding -n kubeflow
```

### 10. Upload training data to S3 bucket: you need at least two files -- at least one for training and one for validation

* create the bucket
    ```bash
    export BUCKET=bucket-recsys #please replace
    aws s3 mb s3://$BUCKET --region $REGION
    ```

* upload the files to the bucket
    ```bash
    aws s3 cp day_0.parquet s3://$BUCKET/initial_criteo/day_0.parquet && aws s3 cp day_1.parquet s3://$BUCKET/initial_criteo/day_1.parquet
    ```

* expected order:
    ```bash
    s3://bucket-name
        └──initial_criteo
        │   ├──day_0.parquet  
        │   └──day_1.parquet
        │ 
        └──new_data
    ```
Note: `day_0.parquet`and `day_1.parquet` are randomly sampled subsets from one day of the [Criteo 1TB Click Logs dataset](https://ailab.criteo.com/download-criteo-1tb-click-logs-dataset/) that have been converted to parquet files using this [Notebook](https://github.com/NVIDIA-Merlin/NVTabular/blob/v0.7.1-docs/examples/scaling-criteo/01-Download-Convert.ipynb) 
 In the initial training run, `day_0.parquet` and `day_1.parquet` are the train and valid datasets, repectively.


### 11. Create the SQS QUEUE
* create an SQS queue for the monitoring component.

    ```bash
    export QUEUE_NAME='merlin-inference-requests' #please replace

    QUEUE_URL=$(aws sqs create-queue \
        --queue-name $QUEUE_NAME \
        --region $REGION \
        --attributes VisibilityTimeout=300,MessageRetentionPeriod=345600 \
        --query 'QueueUrl' \
        --output text 2>/dev/null || \
        aws sqs get-queue-url --queue-name $QUEUE_NAME --region $REGION --query 'QueueUrl' --output text)

    export QUEUE_ARN=$(aws sqs get-queue-attributes \
        --queue-url "$QUEUE_URL" \
        --attribute-names QueueArn \
        --query 'Attributes.QueueArn' \
        --output text)

    echo "Queue URL: $QUEUE_URL"
    echo "QUEUE_ARN: $QUEUE_ARN"
    ```
    Please save the QUEUE_URL, you are going to need it later.

### 12. Create the policies for S3 and SQS access.
* S3 bucket access policy
```bash
cat > s3-full-${BUCKET}.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${BUCKET}",
        "arn:aws:s3:::${BUCKET}/*"
      ]
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name merlin-s3-${BUCKET}-full \
  --policy-document file://s3-full-${BUCKET}.json
```

* SQS queue access policy
```bash
cat > sqs-full-${QUEUE_NAME}.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sqs:*",
      "Resource": "${QUEUE_ARN}"
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name merlin-sqs-${QUEUE_NAME}-full \
  --policy-document file://sqs-full-${QUEUE_NAME}.json
```


### 13. Update the existing ServiceAccount with an IAM role for S3/SQS access
* first, create the IAM role with the S3/SQS access policies attached. Try not to override the existing ServiceAccount (`merlin-sa`) by using the --role-only flag.
    ```bash
    export NAMESPACE=kubeflow
    export SERVICE_ACCOUNT=merlin-sa
    export ROLE_NAME=merlin-irsa-role
    ```

    ```bash
    eksctl create iamserviceaccount \
    --cluster $CLUSTER \
    --region $REGION \
    --namespace $NAMESPACE \
    --name $SERVICE_ACCOUNT \
    --role-name $ROLE_NAME \
    --attach-policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/merlin-s3-${BUCKET}-full \
    --attach-policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/merlin-sqs-${QUEUE_NAME}-full \
    --role-only \
    --approve
    ```

* then, annotate the existing ServiceAccount with the role.
    ```bash
    kubectl -n $NAMESPACE annotate serviceaccount $SERVICE_ACCOUNT \
    eks.amazonaws.com/role-arn=arn:aws:iam::$AWS_ACCOUNT_ID:role/$ROLE_NAME \
    --overwrite
    ```


### 14. Install Prometheus and Grafana
* Install the kube-prometheus-stack which includes Prometheus Operator, Prometheus, and Grafana.
    ```bash
    export GRAFANA_ADMIN_USERNAME=yourusernameREPLACE # replace
    export GRAFANA_ADMIN_PASSWORD=yourChosenPasswordREPLACE # replace
    ```

    ```bash
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update
    helm install prometheus prometheus-community/kube-prometheus-stack \
        -n monitoring \
        --create-namespace \
        --set grafana.adminUser=$GRAFANA_ADMIN_USERNAME \
        --set grafana.adminPassword=$GRAFANA_ADMIN_PASSWORD
    ```  

* you can always fetch your username and password:
    ```bash
    kubectl --namespace monitoring get secrets prometheus-grafana -o jsonpath="{.data.admin-user}" | base64 -d ; echo
    kubectl --namespace monitoring get secrets prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 -d ; echo 
    ```
* Update Prometheus to scrape all ServiceMonitors where release is either "triton" or "prometheus":
    ```bash
    helm upgrade prometheus prometheus-community/kube-prometheus-stack -n monitoring \
    --reuse-values \
    --set-json 'prometheus.prometheusSpec.serviceMonitorSelector={"matchExpressions":[{"key":"release","operator":"In","values":["prometheus","triton"]}]}'
    ```
    This ensures it is able to scrape `release: triton` ServiceMonitors; as well as its own internal components with: `release: prometheus`

* Verify installation:
    ```bash
    kubectl get pods -n monitoring
    kubectl get crd | grep monitoring.coreos.com
    ```

* Access Grafana (optional):
    ```bash
    kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80
    # Open http://localhost:3000 (admin/admin)
    ```

### 15. Build and push containers to ECR
#### 1. Data extraction & Triton deployment container:  
* navigate to the project root, then run the scripts below (ensure to replace `AWS_ACCOUNT_ID` and `REGION`):
    ```bash
    chmod +x build_copy_container_aws.sh
    ./build_copy_container_aws.sh AWS_ACCOUNT_ID REGION
    ```
    this builds the image, tags it and pushes it to ECR repo. The URI of the pushed image is saved to .image_uris/data_extraction.txt  
    This container will also be used to deploy the Triton inference server.

#### 2. Data validation container
* navigate to the project root, then run the scripts below (ensure to replace `AWS_ACCOUNT_ID` and `REGION`):
    ```bash
    chmod +x build_validation_component.sh
    ./build_validation_component.sh AWS_ACCOUNT_ID REGION
    ```
    this builds the image, tags it and pushes it to ECR repo. The URI of the pushed image is saved to .image_uris/data_validation.txt

#### 3. Preprocessing & Training container
* navigate to the project root, then run the scripts below (ensure to replace `AWS_ACCOUNT_ID` and `REGION`):  
    ```bash
    chmod +x build_training_container.sh
    ./build_training_container.sh AWS_ACCOUNT_ID REGION
    ```
    this builds the image, tags it 0.5.1 and pushes it to ECR repo. The URI of the pushed image is saved to .image_uris/training.txt


#### 4. Inference container
* Pull, tag, and push the merlin-inference:0.5.1 container to ECR. Navigate to the project root, then run the scripts below (ensure to replace `AWS_ACCOUNT_ID` and `REGION`)  
    ```bash
    chmod +x push_inference_container.sh
    ./push_inference_container.sh AWS_ACCOUNT_ID REGION
    ```
#### 5. Deployment container
* The data extraction container will be used by the Kubeflow pipeline to deploy the Triton inference server.

#### 6. Monitoring container
* save the kfp client in text file `kfp_client_host_key.txt` in the monitoring folder.
* navigate to the project root, then run the scripts below (ensure to replace `AWS_ACCOUNT_ID` and `REGION`): 
    ```bash
    chmod +x build_monitoring_container_aws.sh
    ./build_monitoring_container_aws.sh AWS_ACCOUNT_ID REGION
    ```

### 16. Deploy Cluster Autoscaler
I recommend visiting the offical resources for best practices and adjust the configuration to suit your needs: [Cluster Autoscaler on AWS](https://docs.aws.amazon.com/eks/latest/best-practices/cas.html) and [Cluster Autoscaler Setup instructions](https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/cloudprovider/aws/README.md#auto-discovery-setup).
 
i.  deploy with helm
```bash
helm repo add autoscaler https://kubernetes.github.io/autoscaler
helm install cluster-autoscaler autoscaler/cluster-autoscaler \
  --namespace kube-system \
  --set autoDiscovery.clusterName=$CLUSTER \
  --set awsRegion=$REGION
```

ii. create a least privilege IAM policy for the Cluster Autoscaler. This scopes destructive actions (`SetDesiredCapacity`, `TerminateInstanceInAutoScalingGroup`) to only ASGs tagged for this cluster, while allowing read-only describe actions on all resources.
```bash
cat > cluster-autoscaler-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:SetDesiredCapacity",
        "autoscaling:TerminateInstanceInAutoScalingGroup"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/k8s.io/cluster-autoscaler/enabled": "true",
          "aws:ResourceTag/k8s.io/cluster-autoscaler/${CLUSTER}": "owned"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:DescribeAutoScalingInstances",
        "autoscaling:DescribeLaunchConfigurations",
        "autoscaling:DescribeScalingActivities",
        "autoscaling:DescribeTags",
        "ec2:DescribeImages",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:GetInstanceTypesFromInstanceRequirements",
        "eks:DescribeNodegroup"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam create-policy \
  --policy-name cluster-autoscaler-${CLUSTER} \
  --policy-document file://cluster-autoscaler-policy.json
```

iii. create a service account (`cluster-autoscaler`) with the least privilege policy attached.
```bash
eksctl create iamserviceaccount \
  --cluster=$CLUSTER \
  --namespace=kube-system \
  --name=cluster-autoscaler \
  --attach-policy-arn=arn:aws:iam::${AWS_ACCOUNT_ID}:policy/cluster-autoscaler-${CLUSTER} \
  --override-existing-serviceaccounts \
  --approve \
  --region=$REGION
```

iv. upgrade Helm release to use the service account
```bash
helm upgrade cluster-autoscaler autoscaler/cluster-autoscaler \
  --namespace kube-system \
  --set autoDiscovery.clusterName=$CLUSTER \
  --set awsRegion=$REGION \
  --set rbac.serviceAccount.create=false \
  --set rbac.serviceAccount.name=cluster-autoscaler
```

### 17. Deploy the Horizontal Pod Autoscaler
* create the `custom-metrics` namespace. Some of the the manifests reference this namespace; the namedspaced resources including ConfigMap, Deployment, ServiceAccount, Service will fail to create without it.

    ```bash
    kubectl create namespace custom-metrics
    ```
* apply [custom-metric-server-config.yaml](scaling_yamls/custom-metric-server-config.yaml)
    ```bash
    kubectl apply -f custom-metric-server-config.yaml
    ```
    Deploys the ConfigMap which defines the translation rule i.e.,
    
    - Uses the raw triton metrics `nv_inference_queue_duration_us` and `nv_inference_request_success`, to compute **`avg_time_queue_ms`**.  

    - **`avg_time_queue_ms`** = `'avg(delta(nv_inference_queue_duration_us{<<.LabelMatchers>>}[30s])/(1+delta(nv_inference_request_success{<<.LabelMatchers>>}[30s]))/1000) by (<<.GroupBy>>)'` and exposes this `avg_time_queue_ms`to HPA.

* apply [custom-metric-server-rbac.yaml](scaling_yamls/custom-metrics-server-rbac.yaml): role-based access control (RBAC) 
    ```bash
    kubectl apply -f custom-metric-server-rbac.yaml
    ```
    - allows custom metrics adapter to read kubernetes resources and auth config; allows HPA controller to read custom metrics API.

* apply [custom-metric-server.yaml](scaling_yamls/custom-metric-server.yaml)
    ```bash
    kubectl apply -f custom-metric-server.yaml
    ```
    - runs the Prometheus Adapter pod and points it to Prometheus:  
        `http://<PROMETHEUS_SERVICE_NAME>.<PROMETHEUS_NAMESPACE>.svc.cluster.local:9090`
    - mounts the adapter config deployed earlier.
    - registers APIService custom.metrics.k8s.io so Kubernetes/HPA can query it.

* apply [triton-hpa.yaml](scaling_yamls/triton-hpa.yaml)
    ```bash
    kubectl apply -f triton-hpa.yaml
    ```
    - creates horizontal pod autoscaler (HPA) resource which adjusts the number of pods in the specified target (the triton-triton-inference-server deployment) based on the custom metric (`avg_time_queue_ms`) that was exposed for the kubeflow namespace by the prometheus custom metrics adapter. If the metric value exceeds 200 milliseconds, the HPA will scale up the number of replicas in the target to 2 and will scale down to 1 if metric falls below 200 miiliseconds.


### 18. Compile Kubeflow Pipeline
* start by installing the kubeflow pipelines SDK (`kfp`) and `kfp-kubernetes` on your gateway node.
    * create a conda environment and activate it
        ```bash
        conda create -n kfp-env python=3.12
        conda activate kfp-env
        ```

    * Install kubeflow pipelines and kfp-kubernetes
        ```bash
        pip install kfp==2.15.2
        pip install kfp-kubernetes==2.15.2
        ```
* compile pipeline
    ```bash
    python merlin-pipeline.py \
    -dexc "$(cat .image_uris/data_extraction.txt)" \
    -dvc "$(cat .image_uris/validation.txt)" \
    -ptc "$(cat .image_uris/training.txt)" \
    -dc "$(cat .image_uris/data_extraction.txt)" \
    -mc "$(cat .image_uris/monitoring.txt)"
    ```
    This creates `merlin-pipeline-aws.yaml` 
* upload this yaml in the Kubeflow UI:
    ```bash
    kubectl port-forward -n kubeflow svc/ml-pipeline-ui 8080:80
    ```
    The kfp UI can be accessed at:  http://localhost:8080

* You should see this DAG
    ![Kubelow UI showing Pipeline DAG](../static/pipeline_tasks.png)

* Click `Create experiment` to create an experiment. Then, create `Create run`.
Enter the service account name we created earlier, e.g.,`merlin-sa` in the "Service Account" field. Argo (Workflow) will create the pods with this service account so pods automatically inherit the permissions associated with this service account.

* Fill out all other fields and hit Start:   
    ![Kubeflow UI showing the create run page](../static/pipeline_run_UI.png)


### 19. Test the performance monitor.
* Once the pipeline run in previous step completes, you test the monitoring module by sending inference requests using the sample python app [performance-test.py](client_app/performance-test.py):
    - Ensure to start the app in an environment with `tritonclient` installed. I would recommend running inside a  container like: `nvcr.io/nvidia/merlin/merlin-inference:0.5.1`. Replace the placeholders in the sample command below.

        ```sh
        !python3 performance-test.py \
            --triton_grpc_url <LOAD_BALANCER_URL>:8001 \
            --model_name hugectr_dcn_ens \
            --test_data day_1.parquet \
            --batch_size 64 \
            --n_batches 30 \
            --queue_url <SQS_URL> \
            --verbose False
        ```





    



















