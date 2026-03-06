#!/bin/bash

# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for EKS deployment
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================


AWS_REGION=${1:-"us-east-1"}
S3_BUCKET=${2:-"aws-nvidia-recsys"}
DATA_DIR=${3:-"initial_training_parquet_path_in_s3"}
NEW_DATA_DIR=${4:-"new_data_parquet_path_in_s3_for_live_uploader"}
LOCAL_DATA_DIR=${5:-"/var/lib/data"}
PIPELINE_ID=${6:-"1234567890abcdef"}
SQS_QUEUE_URL=${7:-"your-sqs-queue-url"}
AWS_ACCOUNT_ID=${8:-"123456789012"}
DO_DATA_VALIDATION=${9:-"False"}
SERVICE_ACCOUNT=${10:-"default"}
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

#check monitoring is already deployed
monitoring_status=$(helm status monitoring 2>&1) # to prevent the script from exiting if helm status fails because monitoring is not found
echo "monitoring status check: "
echo "$monitoring_status"

if [[ "$monitoring_status" == "Error: release: not found" ]]; then
    echo "Deploying monitoring module with Helm..."

    MONITORING_IMAGE="$ECR_REGISTRY/merlin/monitoring:0.5.1"
    
    helm install monitoring /script \
        --set aws_region=$AWS_REGION \
        --set image.repository="$MONITORING_IMAGE" \
        --set pipeline_id="$PIPELINE_ID" \
        --set s3_bucket="$S3_BUCKET" \
        --set data_dir="$DATA_DIR" \
        --set new_data_dir="$NEW_DATA_DIR" \
        --set local_data_dir="$LOCAL_DATA_DIR" \
        --set sqs_queue_url="$SQS_QUEUE_URL" \
        --set aws_account_id="$AWS_ACCOUNT_ID" \
        --set do_data_validation="$DO_DATA_VALIDATION" \
        --set service_account="$SERVICE_ACCOUNT"

else
    echo "monitoring is already running, not deploying another instance."
fi