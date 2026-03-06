#!/bin/bash

# Copyright (c) 2021 NVIDIA Corporation. All Rights Reserved.
# Modified by Mustapha Unubi Momoh for Amazon EKS Deployment
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


PV_LOC=${1:-"/var/lib/data"}
VALIDATION=${2:-'False'}

if [ -d "$PV_LOC/stats" ] && [ -f "$PV_LOC/stats/stats.txt" ]; then
    latest_stats=$(ls $PV_LOC/stats/stats*.txt -v 2>/dev/null | tail -n1)
    
    # Determine version number
    if [[ "$latest_stats" == "$PV_LOC/stats/stats.txt" ]]; then
        previous_version=0
    else
        previous_version=$(echo "$latest_stats" | grep -oP 'stats\K[0-9]+')
    fi
    
    new_version=$((previous_version + 1))
    previous_stats_file="$latest_stats"
    
    # Generate stats for random new file
    new_file=$(ls $PV_LOC/criteo-data/new_data/ | shuf -n 1)
    echo "Generating stats for new training data: $new_file..."
    python3 -u /script/generate-stats.py \
        --data_dir "$PV_LOC/criteo-data/new_data/$new_file" \
        --output_dir "$PV_LOC/stats/" \
        --file_name "stats${new_version}.txt"
    
    echo "Validating drift between runs $previous_version and $new_version..."
    python3 -u /script/validate-stats.py \
        --stats_file_1 "$previous_stats_file" \
        --stats_file_2 "$PV_LOC/stats/stats${new_version}.txt"
else
    if [[ "$VALIDATION" == 'True' ]]; then
        mkdir -p "$PV_LOC/stats/"
        
        echo "Generating baseline stats from day_0.parquet..."
        python3 -u /script/generate-stats.py \
            --data_dir "$PV_LOC/criteo-data/crit_int_pq/day_0.parquet" \
            --output_dir "$PV_LOC/stats/" \
            --file_name "stats.txt"
    else
        echo "Skipping validation..."
    fi
fi