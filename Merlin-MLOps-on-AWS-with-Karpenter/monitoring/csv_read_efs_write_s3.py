# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for EKS Deployment

import pandas as pd
from glob import glob
import os
from datetime import datetime
import sys
import boto3
from botocore.exceptions import ClientError
import argparse
from time import sleep
class S3Store:
    def __init__(self, bucket_name, bucket_path, aws_region):
        self.bucket_name = bucket_name
        self.bucket_path = bucket_path
        self.aws_region = aws_region
        self.s3_client = boto3.client('s3', region_name=self.aws_region)
    
    def list_bucket(self, limit=sys.maxsize):
        "List objects in S3 bucket with prefix bucket_path"
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=self.bucket_path)

            count=0
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        print(obj['Key'])
                        count+=1
                        if count >= limit:
                            return
        except ClientError as e:
            print(f"Error listing bucket: {e}")
    
    def upload_to_bucket(self, input_file_name, output_file_name):
        "Upload file to S3 bucket"
        try:
            self.s3_client.upload_file(input_file_name, self.bucket_name, os.path.join(self.bucket_path, output_file_name))
            return True
        except ClientError as e:
            print(f"Error uploading file: {e}")
            return False

def get_local_files(path):
    local_files = glob(path + "/*")
    return local_files

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--local_data_dir",
                        type=str,
                        required=True,
                        default="/var/lib/data",
                        help="Path to new data in the PV")
    
    parser.add_argument("--sleep_time",
                        type=int,
                        required=True,
                        default=60,
                        help="Sleep time in seconds")
    
    parser.add_argument("--s3_bucket",
                        type=str,
                        required=True,
                        default="aws-nvidia-recsys",
                        help="Name of S3 bucket")
    
    parser.add_argument("--bucket_path",
                        type=str,
                        required=True,
                        default="new_data",
                        help="Path of directory to store files on S3 bucket")
    
    parser.add_argument("--aws_region",
                        type=str,
                        required=True,
                        default="us-east-1",
                        help="AWS region")

args = parser.parse_args()

sleep_time = args.sleep_time

#archive the paths in new_data_paths.txt to arhived folder in the bucket before starting the upload loop
s3_store = S3Store(args.s3_bucket, args.bucket_path, args.aws_region)

print(f"Uploading to S3:  s3://{args.s3_bucket}/{args.bucket_path}")

while True:
    sleep(sleep_time)
    local_files = get_local_files(os.path.join(args.local_data_dir, "temp_storage")) #the full path is /var/lib/data/temp_storage, which is the directory where the monitoring container writes new data files to be uploaded to S3

    if len(local_files) == 0:
        print(f"No files to process at {datetime.now()}. Sleeping for {sleep_time} secs")
        continue
    
    print(f"New files found at {datetime.now()}. Pushing to S3...")
    for each_file in local_files:
        s3_path = f"s3://{args.s3_bucket}/{args.bucket_path}/{os.path.basename(each_file)}"
        success = s3_store.upload_to_bucket(each_file, os.path.basename(each_file))

        if success:
            print(f"Uploaded {each_file} to {s3_path} at {datetime.now()}.\nDeleting {each_file} from {args.local_data_dir}/temp_storage")
            try:
                os.remove(each_file)
            except Exception as e:
                print(f"Error deleting file {each_file}: {e}")
        else:
            print(f"Failed to upload {each_file}")