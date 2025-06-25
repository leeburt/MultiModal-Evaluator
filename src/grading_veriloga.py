import os
import json
import re
import random
from collections import defaultdict
from openai import OpenAI # Import the OpenAI library
import traceback
import aiohttp
from typing import Dict, Any, List, Tuple, Optional
import time 

import sys 
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import Config
import asyncio

# --- Configuration ---
# JSON_DIR should point to the directory containing your label data in JSON format
JSON_DIR = "./json_data_openai" # Example path for testing

# --- Class Definitions ---

class VerilogAParser:
    """Parses Verilog-A code to extract modules, ports, and instantiations."""

    def __init__(self):
        # 修改模块定义的正则表达式，支持多行格式和参数列表中的端口声明
        self.module_def_pattern = re.compile(
            r"module\s+([\w\d_]+)\s*\((.*?)\);(.*?)endmodule",
            re.DOTALL | re.IGNORECASE
        )
        
        # 更新端口声明模式，支持以下格式：
        # 1. input/output/inout <name>
        # 2. electrical <name>
        # 3. 支持多行声明
        # 4. 支持端口列表
        self.port_declaration_pattern = re.compile(
            r"^\s*(input|output|inout|electrical)\b\s+([^;]+);",
            re.DOTALL | re.MULTILINE | re.IGNORECASE
        )
        
        # 添加参数列表中的端口声明模式，支持类型声明和注释
        self.port_list_declaration_pattern = re.compile(
            r"(input|output|inout)\s+(?:real|integer|electrical|wire|reg)?\s*([\w\d_]+)",
            re.IGNORECASE
        )
        
        # 添加端口列表解析模式
        self.port_list_pattern = re.compile(
            r"([\w\d_]+)(?:\s*,\s*([\w\d_]+))*",
            re.IGNORECASE
        )
        
        # Regex for instantiations: Module_type Instance_name (.port(net), ...);
        self.instance_pattern = re.compile(
            r"^\s*([\w\d_]+)\s+([\w\d_]+)\s*\((.*?)\);",
            re.DOTALL | re.MULTILINE | re.IGNORECASE
        )
        
        # Regex for port connections: .InstancePort(NetName)
        self.port_connection_pattern = re.compile(
            r"\.(\w+)\s*\(([\w\d_\[\]:]+)\)",
            re.IGNORECASE
        )

    def parse_ports(self, ports_str):
        """解析端口声明字符串，返回端口列表"""
        ports = []
        for line in ports_str.split(','):
            line = line.strip()
            if line:
                # 移除注释部分
                if '//' in line:
                    line = line.split('//')[0].strip()
                ports.extend([p.strip() for p in line.split(',')])
        return ports

    def parse(self, code):
        """Parses the Verilog-A code string."""
        modules_info = {}
        top_level_instantiations = []
        top_level_module_name = None

        raw_modules = []
        for match in self.module_def_pattern.finditer(code):
            module_name = match.group(1)
            ports_header = match.group(2)
            body_str = match.group(3)
            
            # 解析端口声明
            ports = {
                "inputs": [],
                "outputs": [],
                "inouts": [],
                "electrical": [],
                "all_ports": []
            }
            
            # 解析参数列表中的端口声明
            port_directions = {}  # 用于存储端口的方向信息
            port_types = {}       # 用于存储端口的类型信息
            
            # 处理参数列表中的端口声明
            # 首先移除注释
            ports_header_clean = re.sub(r'//.*$', '', ports_header, flags=re.MULTILINE)
            for port_decl in self.port_list_declaration_pattern.finditer(ports_header_clean):
                decl_type = port_decl.group(1).lower()
                port_name = port_decl.group(2).strip()
                
                port_directions[port_name] = decl_type
                if decl_type == "input":
                    ports["inputs"].append(port_name)
                elif decl_type == "output":
                    ports["outputs"].append(port_name)
                elif decl_type == "inout":
                    ports["inouts"].append(port_name)
                ports["all_ports"].append(port_name)
            
            # 处理模块体中的端口声明
            for port_decl in self.port_declaration_pattern.finditer(body_str):
                decl_type = port_decl.group(1).lower()
                port_names = self.parse_ports(port_decl.group(2))
                
                if decl_type in ["input", "output", "inout"]:
                    for port_name in port_names:
                        port_directions[port_name] = decl_type
                        if decl_type == "input":
                            ports["inputs"].append(port_name)
                        elif decl_type == "output":
                            ports["outputs"].append(port_name)
                        elif decl_type == "inout":
                            ports["inouts"].append(port_name)
                elif decl_type == "electrical":
                    for port_name in port_names:
                        port_types[port_name] = "electrical"
                        if port_name not in ports["electrical"]:
                            ports["electrical"].append(port_name)
            
            raw_modules.append({
                "name": module_name,
                "ports": ports,
                "body_str": body_str,
                "start_index": match.start()
            })

        if not raw_modules:
            return {"modules": {}, "top_level_module_name": None, "top_level_instantiations": [], "raw_code": code}

        # Determine top-level module
        if len(raw_modules) == 1:
            top_level_module_name = raw_modules[0]['name']
        else:
            potential_top_modules = {}
            module_names_defined = {m['name'] for m in raw_modules}
            last_module_in_file = raw_modules[-1]['name'] if raw_modules else None

            for m_data in raw_modules:
                score = 0
                for other_m_name in module_names_defined:
                    if other_m_name != m_data['name'] and \
                       re.search(r"\b" + re.escape(other_m_name) + r"\s+\w+", m_data['body_str']):
                        score += 1
                if "Top-Level".lower() in m_data['name'].lower() or \
                   "Top_Level".lower() in m_data['name'].lower() or \
                   (m_data['start_index'] > 0 and "Top-Level".lower() in code[max(0, m_data['start_index']-60):m_data['start_index']].lower()) or \
                   (m_data['start_index'] > 0 and "Top_Level".lower() in code[max(0, m_data['start_index']-60):m_data['start_index']].lower()):
                    score += 10
                if m_data['name'] == last_module_in_file and score == 0 and len(raw_modules) > 1:
                    score += 1
                if score > 0:
                    potential_top_modules[m_data['name']] = score
            
            if potential_top_modules:
                top_level_module_name = max(potential_top_modules, key=potential_top_modules.get)
            elif last_module_in_file:
                top_level_module_name = last_module_in_file
            else:
                top_level_module_name = raw_modules[0]['name']

        # --- Module and Port Parsing ---
        for match in raw_modules:
            module_name = match['name']
            body_str = match['body_str']
            ports = match['ports']
            
            # 使用已经解析好的端口信息
            parsed_ports = ports
            
            modules_info[module_name] = {"ports": parsed_ports, "type": "sub_module"}

            if module_name == top_level_module_name:
                modules_info[module_name]["type"] = "top_level"
                for inst_match in self.instance_pattern.finditer(body_str):
                    inst_type, inst_name, inst_ports_str = inst_match.groups()
                    connections = {
                        conn_match.group(1): conn_match.group(2)
                        for conn_match in self.port_connection_pattern.finditer(inst_ports_str)
                    }
                    top_level_instantiations.append({
                        "instance_name": inst_name,
                        "module_type": inst_type,
                        "connections": connections
                    })
        
        return {
            "modules": modules_info,
            "top_level_module_name": top_level_module_name,
            "top_level_instantiations": top_level_instantiations,
            "raw_code": code
        }

    def get_module_signatures(self, parsed_code):
        """Extracts (name, num_inputs, num_outputs, num_inouts) for sub-modules."""
        signatures = {}
        for name, data in parsed_code.get("modules", {}).items():
            if data.get("type") == "sub_module":
                signatures[name] = (
                    len(data.get("ports", {}).get("inputs", [])),
                    len(data.get("ports", {}).get("outputs", [])),
                    len(data.get("ports", {}).get("inouts", []))  # 添加inout端口数量
                )
        return signatures

    def get_port_lists(self, parsed_code, module_name):
        """Gets input, output, and inout port lists for a given module name."""
        module_data = parsed_code.get("modules", {}).get(module_name)
        if module_data:
            return {
                "inputs": module_data.get("ports", {}).get("inputs", []),
                "outputs": module_data.get("ports", {}).get("outputs", []),
                "inouts": module_data.get("ports", {}).get("inouts", [])  # 添加inout端口列表
            }
        return None

class LLMHelper:
    """Handles interaction with the OpenAI API for semantic mapping."""

    def __init__(self,base_url=None, api_key=None, model="o4-mini",session=None,config=None):

        if not api_key:
            raise ValueError("OpenAI API key not found. Set OPENAI_API_KEY environment variable.")

        self.client = OpenAI(api_key=api_key,base_url=base_url)
        self.model = model
        self.api_key = api_key
        self.session = session
        self.config = config
        self.base_url = base_url


    async def _query_openai(self, prompt_text, system_message="You are an expert in Verilog-A and circuit design."):
        """Private method to send a query to OpenAI and get a JSON response."""
        # print(f"\n--- Sending Query to OpenAI ({self.model}) ---")
        # print(f"System: {system_message}")
        # print(f"User Prompt (first 200 chars): {prompt_text[:200]}...")

        if self.session:
            result = await self.generate(self.session,prompt_text)
            if "error" in result:
                print(f"Error in generate: {result['error']}")
                return {}
            content = result.get("content", "")
            # print(f"--- OpenAI Response (Raw) ---\n{content}\n-----------------------------")
            if "```json" in content:
                content = re.sub(r'```json\n|\n```', '', content)
            else:
                content = content
            try:
                content = json.loads(content)
                # print(f"--- OpenAI Response (Parsed) ---\n{content}\n-----------------------------")
                return content
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON from content: {e}")
                print(f"Content: {content}")
                return {}
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content":  prompt_text}
                ],
                response_format={"type": "json_object"}, # Requires newer models
                temperature=0.2 , # Lower temperature for more deterministic output
                stream=False
            )
            content = response.choices[0].message.content
            # print(f"--- OpenAI Response (Raw) ---\n{content}\n-----------------------------")
            if "```json" in content:
                content = re.sub(r'```json\n|\n```', '', content)
            else:
                content = content
            content = json.loads(content)
            # print(f"--- OpenAI Response (Parsed) ---\n{content}\n-----------------------------")
            return content
        except Exception as e:
            print(traceback.format_exc())
            print(f"Error querying OpenAI or parsing JSON response: {e}")
            return {} # Return empty dict on error

    async def generate(self, session:aiohttp.ClientSession, prompt: str, image_base64: str=None) -> Dict[str, Any]:
        """Generate a response from the OpenAI API."""
        """Generate response from answer API"""
        start_time = time.time()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        if image_base64:
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_base64, "detail": "high"}}
                ]}
            ]
        else:
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                ]}
            ]
        
        # 准备基础参数
        data = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},  # 新增这一行
        }
        
        
        # 添加可选生成参数 - 仅添加终端指定的参数
        if hasattr(self.config, 'answer_temperature') and self.config.answer_temperature is not None:
            data["temperature"] = self.config.answer_temperature
        if hasattr(self.config, 'answer_max_tokens') and self.config.answer_max_tokens is not None:
            data["max_tokens"] = self.config.answer_max_tokens
        if hasattr(self.config, 'answer_top_k') and self.config.answer_top_k is not None:
            data["top_k"] = self.config.answer_top_k

        if "o4-mini" not in self.model and "o3" not in self.model:
            if hasattr(self.config, 'answer_top_p') and self.config.answer_top_p is not None:
                data["top_p"] = self.config.answer_top_p
        
        try:
            async with session.post(f"{self.base_url}/chat/completions", 
                                    headers=headers, 
                                    json=data,
                                    timeout=600) as response:
                
                response_status = response.status
                if response_status != 200:
                    error_text = await response.text()
                    print(f"API Error: Status {response_status}")
                    print(f"Error details: {error_text}")
                    return {"error": f"API Error: {response_status}", "content": "", "usage": {}, "latency": time.time() - start_time}
                    
                try:
                    response_json = await response.json()
                    end_time = time.time()
                    response_time = end_time - start_time
                    
                    if "choices" in response_json and len(response_json["choices"]) > 0:
                        result = {
                            "content": response_json["choices"][0]["message"]["content"],
                            "usage": response_json.get("usage", {}),
                            "latency": response_time
                        }
                        return result
                    else:
                        print(f"Invalid response structure: {response_json}")
                        return {"error": "Invalid API response structure", "content": "", "usage": {}, "latency": end_time - start_time}
                except Exception as e:
                    response_text = await response.text()
                    print(f"Failed to parse API response: {e}")
                    print(f"Raw response: {response_text}")
                    return {"error": f"Failed to parse API response: {str(e)}", "content": "", "usage": {}, "latency": time.time() - start_time}
        except Exception as e:
            print(f"verilog-a grading API request error: {str(e)}")
            return {"error": str(e), "content": "", "usage": {}, "latency": time.time() - start_time}

    async def map_module_names(self, label_signatures, llm_signatures, label_top_module, llm_top_module):
        """Maps module names between label and LLM outputs using OpenAI."""
        prompt = f"""
            Analyze the following two sets of Verilog-A module signatures.
            Each signature contains (num_inputs, num_outputs, num_inouts).
            Set 1 (Label): {json.dumps(label_signatures, indent=2)}
            Label's Top-Level Module: {label_top_module}

            Set 2 (LLM Output): {json.dumps(llm_signatures, indent=2)}
            LLM's Top-Level Module: {llm_top_module}

            Your task is to map module names from Set 1 (Label) to Set 2 (LLM Output).
            Prioritize matching I/O counts (including inout ports) and semantically similar names (e.g., Adder to Summer, Integrator to Integration).
            The top-level modules ({label_top_module} and {llm_top_module}) should also be mapped if they appear in the signatures or as distinct top-level entities.
            Ensure the output is a single JSON object where keys are module names from Set 1 (Label) and values are the corresponding module names from Set 2 (LLM Output).
            Example: {{ "Adder_1": "Summer_1", "{label_top_module}": "{llm_top_module}"}}
            If a label module has no clear match, do not include it in the JSON.
            """
        # print("map_module_names prompt:",prompt)
        return await self._query_openai(prompt)

    async def map_port_names(self, label_module_name, label_ports, llm_module_name, llm_ports):
        """Maps port names for a given pair of modules using OpenAI."""
        prompt = f"""
                Given two Verilog-A modules:
                1. Label Module: '{label_module_name}'
                Inputs: {json.dumps(label_ports.get('inputs',[]))}
                Outputs: {json.dumps(label_ports.get('outputs',[]))}
                Inouts: {json.dumps(label_ports.get('inouts',[]))}

                2. LLM Module: '{llm_module_name}'
                Inputs: {json.dumps(llm_ports.get('inputs',[]))}
                Outputs: {json.dumps(llm_ports.get('outputs',[]))}
                Inouts: {json.dumps(llm_ports.get('inouts',[]))}

                Map the port names from the Label Module to the LLM Module. Consider:
                1. Semantic similarity (e.g., VIN to vin, Verr to vs)
                2. Port direction (input to input, output to output, inout to inout)
                3. Port type (electrical, real, etc.)
                Provide the mapping as a single JSON object where keys are port names from the Label Module and values are the corresponding port names from the LLM Module.
                Example: {{ "VIN": "vin", "Verr": "vs" }}
                If a label port has no clear match, do not include it in the JSON.
                """
        return await self._query_openai(prompt)

class VerilogAComparator:
    """Compares two Verilog-A codes (label and LLM output) for modules and connections."""

    def __init__(self,config:Config, parser: VerilogAParser=None, llm_helper: LLMHelper=None):
        self.config = config
        if parser is None:
            self.parser = VerilogAParser()
        else:
            self.parser = parser
        if llm_helper is None:
            self.llm_helper = LLMHelper(
                base_url=config.grading_api_base,
                api_key=config.grading_api_key,
                model=config.grading_model,
                config=config)
        else:
            self.llm_helper = llm_helper
        self.prompt =""


    def _build_connection_graph_2(self, parsed_code, module_mappings, port_mappings):
        """
        通过将端口映射到网络来构建一个规范化的连接图。
        这个修正后的版本能够准确地为电路的网络列表建模，并支持inout端口的双向连接。
        返回三个值：
        1. 转换后的连接对（使用label名称）
        2. 原始连接对（使用原始名称）
        3. 连接映射字典（转换后的连接 -> 原始连接的映射）
        """
        connections = set()
        original_connections = set()  # 存储原始连接对
        connection_mapping = {}  # 新增：存储连接映射关系
        top_module_name_original = parsed_code.get("top_level_module_name")
        instantiations = parsed_code.get("top_level_instantiations", [])

        if not top_module_name_original or not instantiations:
            print("no top_module_name_original or no instantiations")
            return connections, original_connections, connection_mapping

        # --- 确定当前代码的上下文（是Label还是LLM），以便正确映射名称 ---
        is_llm_code_context = top_module_name_original in module_mappings.values()

        # 创建一个从LLM名称到Label名称的反向映射，方便查找
        llm_to_label_module_map = {v: k for k, v in module_mappings.items()}

        def get_label_module_name(original_name):
            if is_llm_code_context:
                return llm_to_label_module_map.get(original_name, original_name)
            return original_name

        def get_label_port_name(module_label_name, port_original_name):
            try:
                if is_llm_code_context:
                    # 在LLM的上下文中，我们需要找到对应的label端口名
                    llm_to_label_port_map = {v: k for k, v in port_mappings.get(module_label_name, {}).items()}
                    return llm_to_label_port_map.get(port_original_name, port_original_name)
            except Exception as e:
                print(f"Error in get_label_port_name: {traceback.format_exc()}:{module_label_name},{port_original_name}")
                return port_original_name   
            # 在Label的上下文中，原始端口名就是label的端口名
            return port_original_name

        def is_driver_port(module_data, port_name):
            """判断一个端口是否可以作为驱动源"""
            return (port_name in module_data["ports"].get("outputs", []) or 
                   port_name in module_data["ports"].get("inouts", []))

        def is_load_port(module_data, port_name):
            """判断一个端口是否可以作为负载"""
            return (port_name in module_data["ports"].get("inputs", []) or 
                   port_name in module_data["ports"].get("inouts", []))

        top_module_label_name = get_label_module_name(top_module_name_original)
        top_module_data = parsed_code["modules"].get(top_module_name_original)
        if not top_module_data:
            return connections, original_connections, connection_mapping

        # --- 构建网络列表的核心数据结构 ---
        # net_map[net_name] = {"drivers": [(模块类型, 端口名), ...], "loads": [(模块类型, 端口名), ...]}
        net_map = defaultdict(lambda: {"drivers": [], "loads": []})
        original_net_map = defaultdict(lambda: {"drivers": [], "loads": []})  # 存储原始连接信息
        node_mapping = {}

        # 1. 将顶层输入和inout视为驱动源
        for port_name_orig in top_module_data["ports"].get("inputs", []) + top_module_data["ports"].get("inouts", []):
            port_label_name = get_label_port_name(top_module_label_name, port_name_orig)
            net_map[port_name_orig]["drivers"].append((f"TOP_LEVEL_INPUT.{top_module_label_name}", port_label_name))
            original_net_map[port_name_orig]["drivers"].append((f"TOP_LEVEL_INPUT.{top_module_name_original}", port_name_orig))
            node_mapping[(f"TOP_LEVEL_INPUT.{top_module_label_name}", port_label_name)] = (f"TOP_LEVEL_INPUT.{top_module_name_original}", port_name_orig)
            

        # 2. 处理所有模块实例化，填充网络列表
        for inst in instantiations:
            inst_module_type_orig = inst["module_type"]
            inst_module_label_name = get_label_module_name(inst_module_type_orig)
            
            inst_module_def = parsed_code["modules"].get(inst_module_type_orig)
            if not inst_module_def:
                continue

            for inst_port_orig, net_name in inst["connections"].items():
                port_label_name = get_label_port_name(inst_module_label_name, inst_port_orig)
                
                # 检查端口类型并添加到相应的列表
                if is_driver_port(inst_module_def, inst_port_orig):
                    net_map[net_name]["drivers"].append((inst_module_label_name, port_label_name))
                    original_net_map[net_name]["drivers"].append((inst_module_type_orig, inst_port_orig))
                    node_mapping[(inst_module_label_name, port_label_name)] = (inst_module_type_orig, inst_port_orig)
                if is_load_port(inst_module_def, inst_port_orig):
                    net_map[net_name]["loads"].append((inst_module_label_name, port_label_name))
                    original_net_map[net_name]["loads"].append((inst_module_type_orig, inst_port_orig))
                    node_mapping[(inst_module_label_name, port_label_name)] = (inst_module_type_orig, inst_port_orig)
        
        # 3. 将顶层输出和inout视为负载
        for port_name_orig in top_module_data["ports"].get("outputs", []) + top_module_data["ports"].get("inouts", []):
            port_label_name = get_label_port_name(top_module_label_name, port_name_orig)
            net_map[port_name_orig]["loads"].append((f"TOP_LEVEL_OUTPUT.{top_module_label_name}", port_label_name))
            original_net_map[port_name_orig]["loads"].append((f"TOP_LEVEL_OUTPUT.{top_module_name_original}", port_name_orig))
            node_mapping[(f"TOP_LEVEL_OUTPUT.{top_module_label_name}", port_label_name)] = (f"TOP_LEVEL_OUTPUT.{top_module_name_original}", port_name_orig)

        # 4. 根据填充好的网络列表，构建最终的、规范化的连接图
        for net_name, drive_load_info in net_map.items():
            drivers = drive_load_info["drivers"]
            loads = drive_load_info["loads"]
            
            if not drivers or not loads:
                # print(f"skip {net_name} because it has no driver or load")
                continue

            # 为每个驱动源和每个负载之间创建连接
            for driver in drivers:
                for load in loads:
                    if driver != load:  # 避免自连接
                        connections.add((driver, load))
                        ori_driver = node_mapping.get(driver)
                        ori_load = node_mapping.get(load)
                        connection_mapping[(driver, load)] = (ori_driver, ori_load)
                        

        # 5. 构建原始连接图和连接映射
        for net_name, drive_load_info in original_net_map.items():
            drivers = drive_load_info["drivers"]
            loads = drive_load_info["loads"]
            
            if not drivers or not loads:
                continue

            # 为每个驱动源和每个负载之间创建原始连接
            for driver in drivers:
                for load in loads:
                    if driver != load:  # 避免自连接
                        original_conn = (driver, load)
                        original_connections.add(original_conn)
                        
        return connections, original_connections, connection_mapping

    def _calculate_precision_recall_f1(self, tp, fp, fn):
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        return precision, recall, f1
    

    def get_module_mappings_score(self,results):
        tp_mod = len(results['module_mappings'])
        fp_mod = len(results['module_mappings_fp'])
        fn_mod = len(results['module_mappings_fn'])
        
        results["module_mappings_metrics"]["tp"] = tp_mod
        results["module_mappings_metrics"]["fp"] = fp_mod
        results["module_mappings_metrics"]["fn"] = fn_mod
        p, r, f1 = self._calculate_precision_recall_f1(tp_mod, fp_mod, fn_mod)
        results["module_mappings_metrics"]["precision"]  = p
        results["module_mappings_metrics"]["recall"]     = r
        results["module_mappings_metrics"]["f1"]         = f1
        return results

    def get_module_score(self,results):
        tp_mod = len(results['match_modules'])
        fp_mod = len(results['FP_modules'])
        fn_mod = len(results['FN_modules'])
        
        results["module_metrics"]["tp"] = tp_mod
        results["module_metrics"]["fp"] = fp_mod
        results["module_metrics"]["fn"] = fn_mod
        p, r, f1 = self._calculate_precision_recall_f1(tp_mod, fp_mod, fn_mod)
        results["module_metrics"]["precision"]  = p
        results["module_metrics"]["recall"]     = r
        results["module_metrics"]["f1"]         = f1

        return results
    
    def get_port_score(self,results,tp_port,fp_port,fn_port):

        results["port_metrics"]["tp"] = tp_port
        results["port_metrics"]["fp"] = fp_port
        results["port_metrics"]["fn"] = fn_port
        p, r, f1 = self._calculate_precision_recall_f1(tp_port, fp_port, fn_port)
        results["port_metrics"]["precision"] = p
        results["port_metrics"]["recall"] = r
        results["port_metrics"]["f1"] = f1
        return results
    
    def get_connection_score(self,results):
        tp_conn = len(results['matched_connections'])
        fp_conn = len(results['FP_connections'])
        fn_conn = len(results['FN_connections'])

        # print("intersection nums:")
        # for conn in results['matched_connections']:
        #     print(conn)
        # print("fp_conn:")
        # for conn in results['FP_connections']:
        #     print(conn)
        # print("fn_conn:")
        # for conn in results['FN_connections']:
        #     print(conn)

        results["connection_metrics"]["tp"] = tp_conn
        results["connection_metrics"]["fp"] = fp_conn
        results["connection_metrics"]["fn"] = fn_conn
        p, r, f1 = self._calculate_precision_recall_f1(tp_conn, fp_conn, fn_conn)
        results["connection_metrics"]["precision"] = p
        results["connection_metrics"]["recall"] = r
        results["connection_metrics"]["f1"] = f1
        return results

    def get_module_match(self,parsed_label,parsed_llm,label_sub_module_names,llm_actual_sub_modules,module_mappings):

        tp_mod = 0
        mapped_llm_modules_in_tp = set()
        mapped_label_modules_in_tp = set()
        match_modules = []
        for label_mod in label_sub_module_names:
            llm_mapped_name = module_mappings.get(label_mod)
            if llm_mapped_name and llm_mapped_name in parsed_llm["modules"]:
                # Check I/O counts for structural similarity after LLM mapping
                label_io = (len(parsed_label["modules"][label_mod]["ports"]["inputs"]), len(parsed_label["modules"][label_mod]["ports"]["outputs"]),len(parsed_label["modules"][label_mod]["ports"]["inouts"]))
                llm_io = (len(parsed_llm["modules"][llm_mapped_name]["ports"]["inputs"]), len(parsed_llm["modules"][llm_mapped_name]["ports"]["outputs"]),len(parsed_llm["modules"][llm_mapped_name]["ports"]["inouts"]))
                if label_io == llm_io:
                    tp_mod += 1
                    mapped_llm_modules_in_tp.add(llm_mapped_name)
                    mapped_label_modules_in_tp.add(label_mod)
                    match_modules.append([label_mod,llm_mapped_name])
        return match_modules,mapped_llm_modules_in_tp,mapped_label_modules_in_tp
    
    def check_connection_correct(self,matched_connections,FP_conections_pair, FN_conections_pair):        ##再次校正
            """
            基于模块级别的匹配来校正连接，处理模块匹配正确但端口匹配错误的情况
            返回：校正后的FP连接、校正后的FN连接、新发现的匹配连接
            """
            # 第一步：提取模块级别的连接
            FP_module_connections = []
            FN_module_connections = []
            
            # 存储原始的模块+端口对，用于后续映射回去
            FP_original_mapping = {}  # 模块连接 -> 原始模块+端口连接列表
            FN_original_mapping = {}  # 模块连接 -> 原始模块+端口连接列表
            
            # 处理FP连接
            for FP_pair in FP_conections_pair:
                module_conn = (FP_pair[0][0], FP_pair[1][0])  # (源模块, 目标模块)
                FP_module_connections.append(module_conn)
                
                # 建立模块连接到原始连接的映射
                if module_conn not in FP_original_mapping:
                    FP_original_mapping[module_conn] = []
                FP_original_mapping[module_conn].append(FP_pair)
            
            # 处理FN连接
            for FN_pair in FN_conections_pair:
                module_conn = (FN_pair[0][0], FN_pair[1][0])  # (源模块, 目标模块)
                FN_module_connections.append(module_conn)
                
                # 建立模块连接到原始连接的映射
                if module_conn not in FN_original_mapping:
                    FN_original_mapping[module_conn] = []
                FN_original_mapping[module_conn].append(FN_pair)
            
            # 第二步：找出模块级别的交集（即模块匹配正确但端口可能错误的连接）
            FP_module_set = set(FP_module_connections)
            FN_module_set = set(FN_module_connections)
            module_intersection = FP_module_set.intersection(FN_module_set)
            
            # 第三步：将模块级别匹配的连接转换为新的匹配连接
            # 这些连接在模块级别是正确的，应该被认为是匹配的
            newly_matched_connections = []
            for module_conn in module_intersection:
                # 从FP中选择一个代表性的连接作为新的匹配连接
                if module_conn in FN_original_mapping:
                    # 选择第一个连接作为代表
                    representative_conn = FN_original_mapping[module_conn][0]
                    newly_matched_connections.append(representative_conn)
            
            # 第四步：移除模块级别匹配的连接，保留真正的FP和FN
            remaining_FP_modules = FP_module_set - module_intersection
            remaining_FN_modules = FN_module_set - module_intersection
            
            # 第五步：将模块级别的连接映射回原始的模块+端口连接
            final_FP_connections = []
            final_FN_connections = []
            
            # 处理剩余的FP连接
            for module_conn in remaining_FP_modules:
                if module_conn in FP_original_mapping:
                    # 如果一个模块连接对应多个端口连接，保留所有的端口连接
                    for original_conn in FP_original_mapping[module_conn]:
                        final_FP_connections.append(original_conn)
            
            # 处理剩余的FN连接  
            for module_conn in remaining_FN_modules:
                if module_conn in FN_original_mapping:
                    # 如果一个模块连接对应多个端口连接，保留所有的端口连接
                    for original_conn in FN_original_mapping[module_conn]:
                        final_FN_connections.append(original_conn)
            
            # 去重处理
            final_FP_connections = list(set(final_FP_connections))
            final_FN_connections = list(set(final_FN_connections))
            newly_matched_connections= list(set(matched_connections+newly_matched_connections))
            return final_FP_connections, final_FN_connections, newly_matched_connections

    

    async def run_comparison(self, label_code_str, llm_code_str):
        """Orchestrates the full comparison process."""
        results = {
            "score": 0,  # 连接映射字典
            "module_metrics": {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0},
            "port_metrics": {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0},
            "connection_metrics": {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0},
            "module_mappings_metrics": {"tp": 0, "fp": 0, "fn": 0, "precision": 0, "recall": 0, "f1": 0},
            "label_code_str": label_code_str,
            "llm_code_str": llm_code_str,
            "parsed_label": {},
            "parsed_llm": {},
            "module_mappings": {},  # 模块映射结果
            "module_mappings_tp": {},  # 正确映射的模块
            "module_mappings_fp": [],  # 错误映射的模块
            "module_mappings_fn": [],  # 未映射的模块
            "match_modules": [],  # 匹配成功的模块
            "FP_modules": [],  # 错误匹配的模块
            "FN_modules": [],  # 未匹配的模块
            "matched_connections": [],  # 匹配成功的连接
            "FP_connections": [],  # 错误匹配的连接
            "FN_connections": [],  # 未匹配的连接
            "label_connections": [],  # 标签中的连接
            "llm_connections": [],  # LLM生成的连接
            "original_llm_connections": [],  # 原始LLM连接
            "connection_mapping": {},  # 连接映射字典
            "errors": []
        }

        parsed_label = self.parser.parse(label_code_str)
        parsed_llm   = self.parser.parse(llm_code_str)

        if not parsed_label.get("modules"):
            results["errors"].append("Failed to parse label code.")
        if not parsed_llm.get("modules"):
            results["errors"].append("Failed to parse LLM code.")
        if results["errors"]:
            return results
        
        results['parsed_label'] = parsed_label
        results['parsed_llm'] = parsed_llm

        ## 模块映射
        label_module_sigs = self.parser.get_module_signatures(parsed_label)
        llm_module_sigs = self.parser.get_module_signatures(parsed_llm)
        
        label_top = parsed_label.get("top_level_module_name", "UnknownLabelTop")
        llm_top = parsed_llm.get("top_level_module_name", "UnknownLLMTop")

        # Add top-level modules to signatures if not already sub-modules for mapping purposes
        if label_top not in label_module_sigs and label_top in parsed_label["modules"]:
            label_top_ports = self.parser.get_port_lists(parsed_label, label_top)
            if label_top_ports:
                 label_module_sigs[label_top] = (
                     len(label_top_ports['inputs']),
                     len(label_top_ports['outputs']),
                     len(label_top_ports['inouts'])
                 )

        if llm_top not in llm_module_sigs and llm_top in parsed_llm["modules"]:
            llm_top_ports = self.parser.get_port_lists(parsed_llm, llm_top)
            if llm_top_ports:
                llm_module_sigs[llm_top] = (
                    len(llm_top_ports['inputs']),
                    len(llm_top_ports['outputs']),
                    len(llm_top_ports['inouts'])
                )

        # 获取模块映射结果
        module_mappings = await self.llm_helper.map_module_names(label_module_sigs, llm_module_sigs, label_top, llm_top)

        print(module_mappings)

        # print(module_mappings)
        results["module_mappings"] = module_mappings
        results["total_correct_components"] = module_mappings
        results["total_generated_components"] = list(label_module_sigs.keys())
        results["total_reference_components"] = list(label_module_sigs.keys())
        results["module_mappings_fp"] = list(set(llm_module_sigs.keys()) - set(module_mappings.values()))
        results["module_mappings_fn"] = list(set(module_mappings.keys()) - set(label_module_sigs.keys()))
        
        # 计算模块映射的精确率、召回率和F1值
        results = self.get_module_mappings_score(results)

        ## 模块匹配
        label_sub_module_names = {m for m, d in parsed_label["modules"].items() if d.get("type") == "sub_module"}
        llm_actual_sub_modules = {m for m, d in parsed_llm["modules"].items() if d.get("type") == "sub_module"}
        match_modules, mapped_llm_modules_in_tp, mapped_label_modules_in_tp = self.get_module_match(
            parsed_label, parsed_llm, label_sub_module_names, llm_actual_sub_modules, module_mappings
        )
        results['match_modules'] = match_modules
        results['FP_modules'] = list(llm_actual_sub_modules - mapped_llm_modules_in_tp)
        results['FN_modules'] = list(label_sub_module_names - mapped_label_modules_in_tp)
        results = self.get_module_score(results)

        ## 端口映射
        port_mappings = {}
        label_port_count = 0
        llm_port_count = 0
        match_port_count = 0
        for label_mod_name, llm_mod_name in module_mappings.items():
            if not label_mod_name or not llm_mod_name: continue
            label_ports = self.parser.get_port_lists(parsed_label, label_mod_name)
            llm_ports   = self.parser.get_port_lists(parsed_llm, llm_mod_name)
            if label_ports is None or llm_ports is None:
                print(f"Error: label_ports or llm_ports is None")
                continue
            ## 计算端口数量
            label_port_count = label_port_count + len(label_ports['inputs']) + len(label_ports['outputs']) + len(label_ports['inouts'])
            llm_port_count = llm_port_count + len(llm_ports['inputs']) + len(llm_ports['outputs']) + len(llm_ports['inouts'])
            if label_ports and llm_ports:
                port_mappings[label_mod_name] = await self.llm_helper.map_port_names(
                    label_mod_name, label_ports, llm_mod_name, llm_ports
                )
                match_port_count = match_port_count + len(port_mappings[label_mod_name])
        port_match_tp = match_port_count
        port_match_fp = llm_port_count - match_port_count
        port_match_fn = label_port_count - match_port_count
        results = self.get_port_score(results,port_match_tp,port_match_fp,port_match_fn)
        results["port_mappings"] = port_mappings

        # Connection Metrics
        label_connections, _, _ = self._build_connection_graph_2(parsed_label, module_mappings, port_mappings)
        llm_connections, ori_llm_connections, connection_label_llm_mapping = self._build_connection_graph_2(parsed_llm, module_mappings, port_mappings)

        results['matched_connections'] = list(label_connections.intersection(llm_connections))
        results['FP_connections'] = list(llm_connections - label_connections)
        results['FN_connections'] = list(label_connections - llm_connections)

        results['FP_connections'], results['FN_connections'], results['matched_connections']  = self.check_connection_correct(results['matched_connections'] ,results['FP_connections'], results['FN_connections'])

        results['label_connections'] = list(label_connections)
        results['llm_connections'] = list(llm_connections)

        results["total_correct_connections"] = results['matched_connections'] 
        results["total_generated_connections"] = list(ori_llm_connections)
        results["total_reference_connections"] = list(label_connections)

        ## concert results['FP_connections'] to llm origin connection
        fp_connections = []
        for conn in results['FP_connections']:
            assert conn in connection_label_llm_mapping, f"Error: {conn} not in connection_label_llm_mapping"
            fp_connections.append(connection_label_llm_mapping[conn])
        results['FP_connections'] = fp_connections
        # print(results['FP_connections'] ,llm_connections)

        
        # 添加原始连接信息
        results['original_llm_connections'] = list(ori_llm_connections)
        # results['connection_mapping'] = connection_label_llm_mapping
        
        # 计算连接的精确率、召回率和F1值
        results = self.get_connection_score(results)

        results = self.format_results(results)
        return results
    
    def calculate_scores(self, result) -> Dict[str, float]:
        """Calculate component and connection scores with partial credit"""
        total_score = result["module_metrics"]["f1"]/3 + result["port_metrics"]["f1"]/3 + result["connection_metrics"]["f1"]/3
        module_mapping_score = result["module_mappings_metrics"]["f1"]
        component_score = result["module_metrics"]["f1"]
        port_score = result["port_metrics"]["f1"]
        connection_score = result["connection_metrics"]["f1"]
        return {
            "total_score": round(total_score*100, 1),
            "module_mapping_score": round(module_mapping_score*100, 1),
            "port_mapping_score": round(port_score*100, 1),
            "component_score": round(component_score*100, 1),
            "connection_score": round(connection_score*100, 1)
        }
    
    def format_results(self,results):
        results['scoring'] = self.calculate_scores(results)
        return results

    async def grade(self,session:aiohttp.ClientSession, prompt: str, llm_code_str: str, label_code_str: str):
        start_time = time.time()
        results = {}
        self.llm_helper.session = session
        try:
            results = await self.run_comparison(label_code_str, llm_code_str)
            final_score = results.get('scoring',{}).get('total_score',0)
        except Exception as e:
            print(traceback.format_exc())
            return {
                "score": 0,
                "content": '',
                "usage": {},
                "latency": round(time.time() - start_time, 2),
                "verilog_a_analysis": results,
                "error": str(traceback.format_exc())
            }
        return {
            "score": final_score,
            "content": '',
            "usage": {},
            "latency": time.time() - start_time,
            "verilog_a_analysis": results
        }
    
def get_llm_result(json_dir):
    """读取JSON文件中的每个问题的结果"""
    try:
        # 读取JSON文件
        with open(json_dir, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        results = {}
        for item in data:
            # 提取对话内容
            conversations = item.get('conversations', [])
            if not conversations:
                continue
                
            # 获取最后一个GPT的回复（通常是Verilog-A代码）
            llm_code = None
            query= None
            for conv in reversed(conversations):
                if conv.get('from') == 'gpt':
                    llm_code = conv.get('value', '')

                if conv.get('from') == 'human':
                    query = conv.get('value', '')
            
            if not llm_code or not query:
                continue
                
            # 提取图片信息
            images = item.get('images', [])
            image_path = images[0] if images else None
            image_path= image_path.split("/")[-1]
            
            # 构建结果字典
            result = {
                'answer': llm_code,
                'images': image_path,
                'query': query
            }
            
            results[image_path]=result
        return results
        
    except Exception as e:
        print(f"读取JSON文件时发生错误: {str(e)}")
        return []
    

def get_benchmark(json_dir):
    """读取JSON文件中的每个问题的结果"""
    try:
        # 读取JSON文件
        with open(json_dir, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        results = {}
        for item in data:
            answer = item.get("answer")
            image_path = item.get("img")
        
            # 构建结果字典
            result = {
                'answer': answer,
                'images': image_path,
            }
            results[image_path]=result
        return results
        
    except Exception as e:
        print(f"读取JSON文件时发生错误: {str(e)}")
        return [] 
    
def get_round_json_files(results_dir):
    """获取所有JSON文件并按score倒序排序"""
    import glob
    files = glob.glob(os.path.join(results_dir, '*.json'))
    
    # 读取每个文件的score信息
    results = {}
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 获取score，如果不存在则设为0
                image_name = data.get('image',{}).get('file','')
                llm_result = data.get('results', {}).get('prompt6', [{}])[0].get('generation', {})
                result = {
                    'answer': llm_result,
                    'images': image_name
                }
                results[image_name] = result
        except (json.JSONDecodeError, IndexError, KeyError):
            # 如果读取失败，score设为0
            print(traceback.format_exc())
            pass 
    
    
    return results

# --- Main Execution (Test Code) ---
if __name__ == "__main__":
    
    try:
        from src.config import Config
        config = Config()
        # --- Initialization ---
        # Pass your OpenAI API Key here or ensure OPENAI_API_KEY env var is set
        # For model, you can use "gpt-4-turbo-preview" or other compatible models
        api_key = os.getenv("OPENAI_API_KEY").strip()
        base_url = os.getenv("OPENAI_BASE_URL").strip()
        print(api_key,base_url)
        
        # 获取所有LLM生成的结果
        # llm_result = get_llm_result(".cache/converted_conversations.json")
        llm_result = get_round_json_files("/data/home/libo/work/MultiModal-Evaluator/benchmark_output/20250623_all_va_data_qwen2.5-vl-32b_v1")
        benchmark = get_benchmark(".cache/system_block_benchmark_v2_verilogA.json")
        # test_list=["14128_2078_block_circuit_train_15k_0321_001118.jpg"] #"2747_block_circuit_train_15k_0321_001209.jpg",
        # test_list = ["2695_block_circuit_train_15k_0321_001159.jpg"]
        # test_list = ["2622_block_circuit_train_15k_0321_001159.jpg"]
        # test_list = ["12980_2061_block_circuit_train_15k_0321_001118.jpg"]
        test_list = ["24325_182_block_circuit_train_15k_0321_000755.jpg"]
        test_list = ["2462_block_circuit_train_15k_0321_001159.jpg"]
        
        results_dir = ".cache/results_soft"
        import shutil
        if not os.path.exists(results_dir):
            os.makedirs(results_dir)
        
        from tools.show_result.show_html import generate_html_report
        
        async def process_results():
            """并发处理所有结果"""
            # 在异步函数中创建session和相关组件
            async with aiohttp.ClientSession() as session:
                # 创建LLMHelper并传入session
                llm_helper = LLMHelper(
                    model="o4-mini",
                    api_key=api_key,
                    base_url=base_url,
                    session=session,  # 传入session
                    config=config
                )
                parser = VerilogAParser()   
                comparator = VerilogAComparator(config=config, parser=parser, llm_helper=llm_helper)
                
                # 创建信号量限制并发数为4
                semaphore = asyncio.Semaphore(4)
                
                async def process_single_result(image_name, llm_info, benchmark_data):
                    """处理单个结果的异步函数"""
                    async with semaphore:  # 限制并发数
                        json_path = f"{results_dir}/{image_name}.json"
                        
                        try:
                            print(f"开始处理图片: {image_name}")
                            
                            if image_name not in benchmark_data:
                                print(f"{image_name} not in benchmark.")
                                return
                            
                            # 使用grade方法，它会自动处理session
                            results = await comparator.grade(
                                session=session,
                                prompt="",  # 这里可以传入空字符串，因为grade方法主要用于评分
                                llm_code_str=llm_info['answer'],
                                label_code_str=benchmark_data[image_name]['answer']
                            )
                            
                            # 从results中提取verilog_a_analysis部分作为主要结果
                            main_results = results.get('verilog_a_analysis', results)

                            # 保存JSON结果
                            with open(json_path, "w", encoding="utf-8") as f:
                                json.dump(main_results, f, ensure_ascii=False, indent=4)

                            # 生成并保存HTML报告
                            html_content = generate_html_report(main_results, image_name)
                            with open(f"{results_dir}/{image_name}.html", "w", encoding="utf-8") as f:
                                f.write(html_content)
                                
                            print(f"完成处理图片: {image_name}")
                            return main_results
                            
                        except Exception as e:
                            print(f"处理 {image_name} 时发生错误: {e}")
                            print(traceback.format_exc())
                            return None
                
                # 创建任务列表
                tasks = []
                count = 0
                
                for image_name, llm_info in llm_result.items():
                    if image_name not in test_list:
                        continue 
                        
                    # 跳过已存在的结果文件
                    json_path = f"{results_dir}/{image_name}.json"
                    # if os.path.exists(json_path):
                    #     continue
                        
                    count += 1
                    if count > 20:
                        break
                        
                    # 创建异步任务
                    task = process_single_result(image_name, llm_info, benchmark)
                    tasks.append(task)
                
                # 并发执行所有任务
                print(f"开始并发处理 {len(tasks)} 个任务...")
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if result is not None:
                        print(result['connection_metrics'])
                print("所有任务处理完成！")
        
        # 运行异步函数
        for i in range(1): 
            print(f"开始第{i+1}次处理...")
            asyncio.run(process_results())
            print(f"第{i+1}次处理完成！")
        
            
    except Exception as e:
        print(traceback.format_exc())