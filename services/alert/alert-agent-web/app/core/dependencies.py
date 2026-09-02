# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import yaml
from its_redis.redis_handler import RedisHandler
from functools import lru_cache

def load_config():
    # Use the CONFIG_PATH environment variable, default to "config.yaml"
    config_file = os.getenv("CONFIG_PATH", "config.yaml")
    with open(config_file, 'r') as file:
        config = yaml.safe_load(file) or {}
    sink_type = os.getenv("ALERT_VLM_ENHANCED_SINK_TYPE")
    if sink_type:
        sink = config.setdefault("vlm_enhanced_sink", {})
        sink["type"] = sink_type
        if sink_type.lower() == "kafka":
            incident = sink.setdefault("incident", {}).setdefault("kafka", {})
            alert = sink.setdefault("alert", {}).setdefault("kafka", {})
            incident["topic"] = os.getenv("ALERT_VLM_INCIDENT_TOPIC", incident.get("topic", "mdx-vlm-incidents"))
            alert["topic"] = os.getenv("ALERT_VLM_ALERT_TOPIC", alert.get("topic", "mdx-vlm-alerts"))
    return config

def load_config_path():
    # Use the CONFIG_PATH environment variable, default to "config.yaml"
    return os.getenv("CONFIG_PATH", "config.yaml")

@lru_cache()
def get_redis_handler() -> RedisHandler:
    """Get or create RedisHandler instance."""
    config_path = load_config_path()  # Get the path to the configuration file
    return RedisHandler(config_path)  # Pass the config file path to RedisHandler 
