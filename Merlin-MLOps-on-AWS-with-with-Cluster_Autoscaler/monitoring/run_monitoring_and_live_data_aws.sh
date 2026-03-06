#!/bin/bash

# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for Amazon EKS deployment
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

# pipeline parameters: currently missing sqs_queue_url, but to be added in next iteration
PIPELINE_PARAMS=$(cat <<EOF
{
    "aws_region": "$AWS_REGION",
    "s3_bucket": "$S3_BUCKET",
    "data_dir": "$DATA_DIR",
    "new_data_dir": "$NEW_DATA_DIR",
    "local_data_dir": "$LOCAL_DATA_DIR",
    "do_data_validation": "$DO_DATA_VALIDATION",
    "aws_account_id": "$AWS_ACCOUNT_ID",
    "sqs_queue_url": "$SQS_QUEUE_URL"
}
EOF
)

LOG_DIR="$LOCAL_DATA_DIR/logs"
mkdir -p $LOG_DIR
UPLOADER_LOG="$LOG_DIR/uploader.log"


# start performance monitor in background
echo "Starting performance monitor (SQS listener)..."
python3 -u /script/perf-monitor-aws.py \
    --aws_region $AWS_REGION \
    --sqs_queue_url $SQS_QUEUE_URL \
    --pipeline_id $PIPELINE_ID \
    --local_data_dir $LOCAL_DATA_DIR \
    --pipeline_params "$PIPELINE_PARAMS" \
    --service_account $SERVICE_ACCOUNT \
    --evaluate_period 500 \
    --acc_threshold 0.61 \
    --min_trigger_len 0.5 \
    --min_log_length 320 \
    --log_time_delta 60 &

# start live data uploader in foreground
python3 -u /script/csv_read_efs_write_s3.py \
    --local_data_dir $LOCAL_DATA_DIR \
    --s3_bucket $S3_BUCKET \
    --bucket_path $NEW_DATA_DIR \
    --aws_region $AWS_REGION \
    --sleep_time 60 \
    2>&1 | tee -a "$UPLOADER_LOG"

UPLOADER_PID=$!
echo "Live data uploader started with PID: $UPLOADER_PID (log: $UPLOADER_LOG)"
