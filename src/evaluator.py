import os
import json
import time
import statistics
import asyncio
import aiohttp
from typing import Dict, List, Any
from tqdm import tqdm
from dataclasses import asdict
from src.config import Config
from src.image_processor import ImageProcessor
from src.model_client import AnswerApiClient
from src.grading_client import VerilogAGradingClient
from src.grading_veriloga import VerilogAComparator
import traceback

class VerilogAEvaluator:
    def __init__(self, config: Config):
        self.config = config
        self.prompts = self._load_prompts()
        
        # 初始化回答模型客户端
        self.answer_api_client = AnswerApiClient(config)
            
        # 使用新的Verilog-A评分客户端
        self.grading_client = VerilogAComparator(config)
        self.grading_client.prompts = self.prompts
        
        # 初始化统计数据结构 - 适配Verilog-A评估
        self.results_stats = {
            "total_samples": 0,
            "successful_samples": 0,
            "failed_samples": 0,
            "scores_by_prompt": {},
            "errors": [],
            "llm_time": 0.0,
            "grade_time": 0.0,
            # Verilog-A特定的累积指标
            "verilog_a_metrics": {
                "total_component_mapping_score": 0.0,
                "total_port_mapping_score": 0.0,
                "total_component_score": 0.0,
                "total_connection_score": 0.0,
                "total_score": 0.0,
                "total_perfect_samples": 0,
                "total_perfect_score": 0.0,
                "total_perfect_ratio": 0,
                "total_correct_components": 0,
                "total_correct_connections": 0,
                "total_generated_components": 0,
                "total_reference_components": 0,
                "total_generated_connections": 0,
                "total_reference_connections": 0
            }
        }
        
        self.individual_results = {}
        self.results = []
        self.file_locks = {}
        
        # 验证和设置要使用的提示键
        if not self.config.prompt_keys:
            self.config.prompt_keys = [k for k in self.prompts.keys() 
                                    if not k.startswith("verilog_a_grading_prompt")]
        else:
            for key in self.config.prompt_keys:
                if key not in self.prompts:
                    raise ValueError(f"指定的提示键'{key}'不存在")
                
        # 为每个提示初始化统计数据
        for key in self.config.prompt_keys:
            self.results_stats["scores_by_prompt"][key] = []

    def _load_prompts(self) -> Dict[str, str]:
        """Load prompts from file"""
        try:
            with open(self.config.prompt_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            raise Exception(f"Failed to load prompts: {str(e)}")
        

    def _load_json(self) -> List[Dict[str, Any]]:
        """Load JSON data"""
        data = []
        try:
            with open(self.config.jsonl_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            raise Exception(f"Failed to load JSON data: {str(e)}")
        return data
    
    def _load_jsonl(self) -> List[Dict[str, Any]]:
        """Load JSONL data"""
        data = []
        try:
            if os.path.splitext(self.config.jsonl_path)[1] == '.json':
                return self._load_json()
            elif os.path.splitext(self.config.jsonl_path)[1] == '.jsonl':
                with open(self.config.jsonl_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data.append(json.loads(line))
                
                if self.config.eval_samples > 0:
                    return data[:self.config.eval_samples]
                return data
        except Exception as e:
            raise Exception(f"Failed to load JSONL data: {str(e)}")

    async def _process_item_with_multiple_runs(self, session, item: Dict[str, Any], prompt_key: str) -> List[Dict[str, Any]]:
        """Process a single item with the specified prompt multiple times"""
        results = []
        
        # Build complete image path
        img_path = os.path.join(self.config.image_root_dir, item["img_folder"], item["img"])
        img_key = f"{item['img_folder']}/{item['img']}"
        
        # Check if file exists
        if not os.path.exists(img_path):
            error_result = {
                "error": f"Image does not exist: {img_path}",
                "item": {
                    "img": item["img"],
                    "img_folder": item["img_folder"]
                },
                "prompt_key": prompt_key,
                "run_id": 0
            }
            self.results_stats["failed_samples"] += 1
            self.results_stats["errors"].append(error_result)
            return [error_result]
        
        # Encode image (only need to do this once)
        try:
            image_base64 = ImageProcessor.encode_image(img_path)
            generation_prompt = self.prompts[prompt_key]
        except Exception as e:
            error_result = {
                "error": str(e),
                "item": {
                    "img": item["img"],
                    "img_folder": item["img_folder"]
                },
                "prompt_key": prompt_key,
                "run_id": 0
            }
            self.results_stats["failed_samples"] += 1
            self.results_stats["errors"].append(error_result)
            return [error_result]
        
        # Execute multiple runs
        run_results = []
        has_successful_run = False
        
        for run_id in range(self.config.runs_per_prompt):
            try:
                # Call model API
                llm_start = time.time()
                llm_result = await self.answer_api_client.generate(session, generation_prompt, image_base64)
                llm_time = time.time() - llm_start
                
                if "error" in llm_result:
                    # Error handling
                    error_result = {
                        "error": f"Generation error: {llm_result.get('error', '')}",
                        "item": {
                            "img": item["img"],
                            "img_folder": item["img_folder"]
                        },
                        "prompt_key": prompt_key,
                        "run_id": run_id
                    }
                    results.append(error_result)
                    self.results_stats["errors"].append(error_result)
                    
                    # If API error, stop execution
                    if "API Error" in llm_result.get('error', ''):
                        raise Exception(f"Critical API error: {llm_result.get('error', '')}")
                    
                    # Add to run results
                    run_results.append({
                        "run_id": run_id,
                        "error": llm_result.get('error', ''),
                        "timestamp": time.time()
                    })
                    continue
                
                # Start Verilog-A grading task
                grading_task = asyncio.create_task(
                    self.grading_client.grade(
                        session,
                        generation_prompt,
                        llm_result["content"],
                        item["answer"]
                    )
                )
                
                # Wait for grading to complete
                grading_result = await grading_task
                grade_time = grading_result.get("latency", 0)
                
                # Ensure results include latency time
                if "latency" not in llm_result:
                    llm_result["latency"] = llm_time
                if "latency" not in grading_result:
                    grading_result["latency"] = grade_time
                
                # Check if grading was successful
                if "error" in grading_result:
                    error_result = {
                        "error": f"Grading error: {grading_result.get('error', '')}",
                        "item": {
                            "img": item["img"],
                            "img_folder": item["img_folder"]
                        },
                        "prompt_key": prompt_key,
                        "run_id": run_id
                    }
                    results.append(error_result)
                    self.results_stats["errors"].append(error_result)
                    
                    # Add to run results
                    run_results.append({
                        "run_id": run_id,
                        "error": grading_result.get('error', ''),
                        "timestamp": time.time()
                    })
                    continue
                
                # Get score and Verilog-A analysis
                score = grading_result.get("score", 0)
                verilog_a_analysis = grading_result.get("verilog_a_analysis", {})
                
                # Mark as successful run
                has_successful_run = True
                
                # 累积Verilog-A指标
                if verilog_a_analysis and "scoring" in verilog_a_analysis:
                    scoring = verilog_a_analysis["scoring"]
                    component_analysis = verilog_a_analysis.get("component_analysis", {})
                    connection_analysis = verilog_a_analysis.get("connection_analysis", {})
                    
                    # 累积分数
                    self.results_stats["verilog_a_metrics"]["total_component_mapping_score"] += scoring.get("module_mapping_score", 0)
                    self.results_stats["verilog_a_metrics"]["total_port_mapping_score"] += scoring.get("port_mapping_score", 0)
                    self.results_stats["verilog_a_metrics"]["total_component_score"] += scoring.get("component_score", 0)
                    self.results_stats["verilog_a_metrics"]["total_connection_score"] += scoring.get("connection_score", 0)
                    self.results_stats["verilog_a_metrics"]["total_score"] += scoring.get("total_score", 0)
                    
                    # 累积组件统计
                    self.results_stats["verilog_a_metrics"]["total_correct_components"] += len(component_analysis.get("correct_components", []))
                    self.results_stats["verilog_a_metrics"]["total_generated_components"] += component_analysis.get("total_generated_components", 0)
                    self.results_stats["verilog_a_metrics"]["total_reference_components"] += component_analysis.get("total_reference_components", 0)
                    
                    # 累积连接统计
                    self.results_stats["verilog_a_metrics"]["total_correct_connections"] += len(connection_analysis.get("correct_connections", []))
                    self.results_stats["verilog_a_metrics"]["total_generated_connections"] += connection_analysis.get("total_generated_connections", 0)
                    self.results_stats["verilog_a_metrics"]["total_reference_connections"] += connection_analysis.get("total_reference_connections", 0)

                    ##全对数量
                    self.results_stats["verilog_a_metrics"]["total_perfect_samples"] += 1 if scoring.get("total_score", 0) >= 100 else 0

                
                # 更新token使用量
                if "usage" not in grading_result:
                    grading_result["usage"] = {}
                grading_result["usage"]["generation_tokens"] = llm_result.get("usage", {}).get("total_tokens", 0)
                grading_result["usage"]["total_tokens"] = (
                    grading_result["usage"].get("grading_tokens", 0) + 
                    grading_result["usage"]["generation_tokens"]
                )
                
                # Integrate results - keep full format for internal use
                full_result = {
                    "item": {
                        "img": item["img"],
                        "img_folder": item["img_folder"],
                        "tag": item.get("tag", "")
                    },
                    "prompt_key": prompt_key,
                    "run_id": run_id,
                    "generation": {
                        "content": llm_result["content"],
                        "usage": llm_result.get("usage", {}),
                        "latency": llm_result.get("latency", llm_time)
                    },
                    "grading": {
                        "content": grading_result.get("content", ""),
                        "score": score,
                        "usage": grading_result.get("usage", {}),
                        "latency": grading_result.get("latency", grade_time),
                        "verilog_a_analysis": verilog_a_analysis
                    },
                    "reference": item["answer"],
                    "timestamp": time.time(),
                    "total_processing_time": llm_time + grade_time
                }
                
                # Create simplified version of results for JSON file
                simplified_result = {
                    "run_id": run_id,
                    "generation": llm_result["content"],
                    "score": score,
                    "grading_feedback": grading_result.get("content", ""),
                    "verilog_a_analysis": verilog_a_analysis,
                    "latency": {
                        "generation": llm_time,
                        "grading": grade_time,
                        "total": llm_time + grade_time
                    },
                    "timestamp": time.time(),
                    "token_usage": {
                        "generation_tokens": llm_result.get("usage", {}).get("total_tokens", 0),
                        "grading_tokens": grading_result.get("usage", {}).get("grading_tokens", 0),
                        "total_tokens": (
                            llm_result.get("usage", {}).get("total_tokens", 0) + 
                            grading_result.get("usage", {}).get("grading_tokens", 0)
                        )
                    }
                }
                
                # Add to run results
                run_results.append(simplified_result)
                
                # Add to internal results list
                results.append(full_result)
                self.results.append(full_result)
                
                # Update statistics
                self.results_stats["scores_by_prompt"][prompt_key].append(score)
                
            except Exception as e:
                # Error handling
                error_result = {
                    "error": f"{str(e)}\n{traceback.format_exc()}",
                    "item": {
                        "img": item["img"],
                        "img_folder": item["img_folder"]
                    },
                    "prompt_key": prompt_key,
                    "run_id": run_id
                }
                results.append(error_result)
                self.results_stats["errors"].append(error_result)
                
                # Add to run results
                run_results.append({
                    "run_id": run_id,
                    "error": str(e),
                    "timestamp": time.time()
                })
        
        # Update sample statistics after all runs
        if has_successful_run:
            self.results_stats["successful_samples"] += 1
            self.results_stats["llm_time"] += llm_time
            self.results_stats["grade_time"] += grade_time
        else:
            self.results_stats["failed_samples"] += 1
        
        # Save individual image results
        if self.config.save_individual:
            try:
                # Ensure output directory exists
                os.makedirs(self.config.output_dir, exist_ok=True)
                
                # 生成包含Verilog-A分数的文件名
                if run_results and "verilog_a_analysis" in run_results[-1] and not "error" in run_results[-1]:
                    last_analysis = run_results[-1]["verilog_a_analysis"]
                    scoring = last_analysis.get("scoring", {})
                    comp_score = scoring.get("component_score", 0)
                    conn_score = scoring.get("connection_score", 0)
                    total_score = scoring.get("total_score", 0)
                    
                    metrics_str = f"comp_{comp_score:.1f}_conn_{conn_score:.1f}_total_{total_score:.0f}"
                else:
                    metrics_str = "comp_0.0_conn_0.0_total_0"
                
                # 创建文件名
                img_name = os.path.splitext(item["img"])[0]  # Remove file extension
                result_file = os.path.join(self.config.output_dir, f"{img_name}_{prompt_key}_{metrics_str}.json")

                # Use lock to get access
                if result_file not in self.file_locks:
                    self.file_locks[result_file] = asyncio.Lock()
                
                async with self.file_locks[result_file]:
                    # Read the latest file content
                    current_img_results = {}
                    if os.path.exists(result_file):
                        try:
                            with open(result_file, 'r', encoding='utf-8') as f:
                                current_img_results = json.load(f)
                        except Exception:
                            # If file is corrupted, create a new one
                            current_img_results = {}
                    
                    # If it's a new file, add basic information
                    if not current_img_results:
                        current_img_results = {
                            "image": {
                                "path": img_key,
                                "file": item["img"],
                                "folder": item["img_folder"],
                                "tag": item.get("tag", "")
                            },
                            "reference": item["answer"],
                            "results": {}
                        }
                    
                    # Ensure results field exists
                    if "results" not in current_img_results:
                        current_img_results["results"] = {}
                        
                    # Add results for current prompt
                    current_img_results["results"][prompt_key] = run_results
                    current_img_results["last_updated"] = time.time()
                    
                    # Save complete results
                    with open(result_file, 'w', encoding='utf-8') as f:
                        json.dump(current_img_results, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error saving individual results: {str(e)}\n{traceback.format_exc()}")
        
        return results

    def update_progress_bar_verilog_a(self, progress_bar, stats):
        """Update progress bar with Verilog-A specific metrics"""
        if self.results_stats["successful_samples"] > 0:
            # 计算当前指标平均值
            successful_count = self.results_stats["successful_samples"]
            verilog_metrics = self.results_stats["verilog_a_metrics"]
            
            avg_total_score = verilog_metrics["total_score"] / successful_count
            avg_node_comp_score = verilog_metrics["total_component_mapping_score"] / successful_count
            avg_port_comp_score = verilog_metrics["total_port_mapping_score"] / successful_count
            avg_comp_score = verilog_metrics["total_component_score"] / successful_count
            avg_conn_score = verilog_metrics["total_connection_score"] / successful_count

            # 计算llm和grade的平均时间
            avg_llm_time = self.results_stats["llm_time"] / successful_count
            avg_grade_time = self.results_stats["grade_time"] / successful_count

            # 计算其他统计信息
            elapsed = time.time() - stats["start_time"]
            samples_per_second =  elapsed / stats["total_samples"]  if  stats["total_samples"] > 0 else 0
            
            progress_desc = (
                f'llm_time: {avg_llm_time:4.2f} | grade_time: {avg_grade_time:4.2f} |'
                f"Completed: {stats['total_samples']} | "
                f"Total: {avg_total_score:.1f}/100 | "
                f"Node Comp: {avg_node_comp_score:.1f}/100 | "
                f"Port Comp: {avg_port_comp_score:.1f}/100 | "
                f"Comp: {avg_comp_score:.1f}/100 | "
                f"Conn: {avg_conn_score:.1f}/100 | {samples_per_second:.2f}/s"
            )
            progress_bar.set_description(progress_desc)
        else:
            # Show basic info if no successful samples yet
            elapsed = time.time() - stats["start_time"]
            samples_per_second = stats["total_samples"] / elapsed if elapsed > 0 else 0
            
            progress_desc = (
                f"Completed: {stats['total_samples']} | "
                f"Processing... | {samples_per_second:.2f}/s"
            )
            progress_bar.set_description(progress_desc)

    def calculate_verilog_a_metrics_average(self) -> Dict[str, float]:
        """Calculate average Verilog-A metrics"""
        if self.results_stats["successful_samples"] == 0:
            return {
                "average_component_score": 0.0,
                "average_connection_score": 0.0,
                "average_total_score": 0.0
            }
        
        successful_count = self.results_stats["successful_samples"]
        verilog_metrics = self.results_stats["verilog_a_metrics"]
        
        return {
            "average_component_score": verilog_metrics["total_component_score"] / successful_count,
            "average_connection_score": verilog_metrics["total_connection_score"] / successful_count,
            "average_total_score": verilog_metrics["total_score"] / successful_count
        }

    def _update_summary(self):
        """Update the summary file with current results"""
        try:
            # Ensure output directory exists
            os.makedirs(self.config.output_dir, exist_ok=True)
            
            # Calculate overall statistics
            all_scores = []
            for scores in self.results_stats["scores_by_prompt"].values():
                all_scores.extend(scores)
            
            # Calculate Verilog-A average metrics
            verilog_a_averages = self.calculate_verilog_a_metrics_average()
            
            # Create summary data
            summary_data = {
                "evaluation_config": asdict(self.config),
                "statistics": {
                    "total_samples": self.results_stats["total_samples"],
                    "successful_samples": self.results_stats["successful_samples"],
                    "failed_samples": self.results_stats["failed_samples"],
                    "success_rate": (self.results_stats["successful_samples"] / self.results_stats["total_samples"]) * 100 if self.results_stats["total_samples"] > 0 else 0,
                    "overall_perfect_ratio": self.results_stats["verilog_a_metrics"]["total_perfect_samples"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "component_mapping_score": self.results_stats["verilog_a_metrics"]["total_component_mapping_score"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "port_mapping_score": self.results_stats["verilog_a_metrics"]["total_port_mapping_score"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "component_score": self.results_stats["verilog_a_metrics"]["total_component_score"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "connection_score": self.results_stats["verilog_a_metrics"]["total_connection_score"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "total_score": self.results_stats["verilog_a_metrics"]["total_score"] / self.results_stats["successful_samples"] if self.results_stats["successful_samples"] > 0 else 0,
                    "overall_average_score": statistics.mean(all_scores) if all_scores else 0,
                    "overall_median_score": statistics.median(all_scores) if all_scores else 0,
                    "overall_std_score": statistics.stdev(all_scores) if len(all_scores) > 1 else 0,
                },
                "verilog_a_metrics": {
                    **verilog_a_averages,
                    "cumulative_metrics": self.results_stats["verilog_a_metrics"]
                },
                "scores_by_prompt": {},
                "errors": self.results_stats["errors"],
                "last_updated": time.time()
            }
            
            # Calculate statistics for each prompt
            for prompt_key, scores in self.results_stats["scores_by_prompt"].items():
                if scores:
                    summary_data["scores_by_prompt"][prompt_key] = {
                        "count": len(scores),
                        "average": statistics.mean(scores),
                        "median": statistics.median(scores),
                        "std": statistics.stdev(scores) if len(scores) > 1 else 0,
                        "min": min(scores),
                        "max": max(scores)
                    }
                else:
                    summary_data["scores_by_prompt"][prompt_key] = {
                        "count": 0,
                        "average": 0,
                        "median": 0,
                        "std": 0,
                        "min": 0,
                        "max": 0
                    }
            
            # Save summary file
            summary_path = os.path.join(self.config.output_dir, self.config.summary_name)
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"Error updating summary file: {str(e)}\n{traceback.format_exc()}")

    def save_verilog_a_metrics_summary(self):
        """Save Verilog-A specific metrics summary"""
        metrics_path = os.path.join(self.config.output_dir, "verilog_a_metrics.json")
        
        # 收集所有样本的详细指标
        sample_metrics = []
        for result in self.results:
            if "grading" in result and "verilog_a_analysis" in result["grading"]:
                analysis = result["grading"]["verilog_a_analysis"]
                scoring = analysis.get("scoring", {})
                component_analysis = analysis.get("component_analysis", {})
                connection_analysis = analysis.get("connection_analysis", {})
                
                sample_metrics.append({
                    "file": result["item"]["img"],
                    "component_score": scoring.get("component_score", 0),
                    "connection_score": scoring.get("connection_score", 0),
                    "total_score": scoring.get("total_score", 0),
                    "correct_components": len(component_analysis.get("correct_components", [])),
                    "generated_components": component_analysis.get("total_generated_components", 0),
                    "reference_components": component_analysis.get("total_reference_components", 0),
                    "correct_connections": len(connection_analysis.get("correct_connections", [])),
                    "generated_connections": connection_analysis.get("total_generated_connections", 0),
                    "reference_connections": connection_analysis.get("total_reference_connections", 0),
                    "prompt": result["prompt_key"]
                })
        
        # 计算总体指标
        if self.results_stats["successful_samples"] > 0:
            successful_count = self.results_stats["successful_samples"]
            verilog_metrics = self.results_stats["verilog_a_metrics"]
            try:
                overall_metrics = {
                    "average_component_mapping_score": verilog_metrics["total_component_mapping_score"] / successful_count,
                    "average_port_mapping_score": verilog_metrics["total_port_mapping_score"] / successful_count,
                    "average_component_score": verilog_metrics["total_component_score"] / successful_count,
                    "average_connection_score": verilog_metrics["total_connection_score"] / successful_count,
                    "average_total_score": verilog_metrics["total_score"] / successful_count,
                    "average_perfect_ratio": verilog_metrics["total_perfect_samples"] / successful_count,
                    "total_perfect_samples": verilog_metrics["total_perfect_samples"],
                    "total_correct_components": verilog_metrics["total_correct_components"],
                    "total_generated_components": verilog_metrics["total_generated_components"],
                    "total_reference_components": verilog_metrics["total_reference_components"],
                    "total_correct_connections": verilog_metrics["total_correct_connections"],
                    "total_generated_connections": verilog_metrics["total_generated_connections"],
                    "total_reference_connections": verilog_metrics["total_reference_connections"]
                }
            except Exception as e:
                print(f"Error saving Verilog-A metrics summary: {traceback.format_exc()},{verilog_metrics}")
                overall_metrics = {}
        else:
            overall_metrics = {}
        
        # 生成汇总数据
        metrics_data = {
            "overall": overall_metrics,
            "samples": sample_metrics,
            "prompt_metrics": {}
        }
        
        # 按提示词计算指标
        for prompt_key in self.config.prompt_keys:
            prompt_results = [r for r in sample_metrics if r["prompt"] == prompt_key]
            if prompt_results:
                metrics_data["prompt_metrics"][prompt_key] = {
                    "average_component_score": sum(r["component_score"] for r in prompt_results) / len(prompt_results),
                    "average_connection_score": sum(r["connection_score"] for r in prompt_results) / len(prompt_results),
                    "average_total_score": sum(r["total_score"] for r in prompt_results) / len(prompt_results),
                    "total_samples": len(prompt_results)
                }
        
        # 保存指标文件
        try:
            with open(metrics_path, 'w', encoding='utf-8') as f:
                json.dump(metrics_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving Verilog-A metrics summary: {str(e)}")

    def save_results(self):
        """Final results saving with Verilog-A specific metrics"""
        # 更新总结文件
        self._update_summary()
        
        # 保存详细的Verilog-A指标
        self.save_verilog_a_metrics_summary()
        
        # 计算实际保存的文件数
        saved_files = 0
        if os.path.exists(self.config.output_dir):
            saved_files = len([f for f in os.listdir(self.config.output_dir) 
                            if f.endswith('.json') and f != self.config.summary_name and f != 'verilog_a_metrics.json'])
        
        print(f"Summary results saved to: {os.path.join(self.config.output_dir, self.config.summary_name)}")
        print(f"Detailed Verilog-A metrics saved to: {os.path.join(self.config.output_dir, 'verilog_a_metrics.json')}")
        print(f"Saved {saved_files} individual image results to {self.config.output_dir} directory")
        
        # 分析总结并打印
        all_scores = []
        for scores in self.results_stats["scores_by_prompt"].values():
            all_scores.extend(scores)
                
        if self.config.verbose and all_scores:
            # Verilog-A评估结果摘要
            print("\n" + "="*50)
            print(" "*15 + "VERILOG-A EVALUATION RESULTS SUMMARY")
            print("="*50)
            print(f"Total samples: {self.results_stats['successful_samples'] + self.results_stats['failed_samples']}")
            print(f"Valid samples: {self.results_stats['successful_samples']}")
            print(f"Error samples: {self.results_stats['failed_samples']}")
            print(f"Overall average score: {statistics.mean(all_scores):.2f}/100")
            
            # 添加Verilog-A指标输出
            if self.results_stats["successful_samples"] > 0:
                print("\n" + "="*50)
                print(" "*15 + "VERILOG-A METRICS SUMMARY")
                print("="*50)
                
                successful_count = self.results_stats["successful_samples"]
                verilog_metrics = self.results_stats["verilog_a_metrics"]
                
                avg_comp_score = verilog_metrics["total_component_score"] / successful_count
                avg_conn_score = verilog_metrics["total_connection_score"] / successful_count
                avg_total_score = verilog_metrics["total_score"] / successful_count
                
                print("Component Analysis:")
                print(f"  Average Score: {avg_comp_score:.2f}/100")
                print(f"  Total Correct Components: {verilog_metrics['total_correct_components']}")
                print(f"  Total Generated Components: {verilog_metrics['total_generated_components']}")
                print(f"  Total Reference Components: {verilog_metrics['total_reference_components']}")
                
                print("\nConnection Analysis:")
                print(f"  Average Score: {avg_conn_score:.2f}/100")
                print(f"  Total Correct Connections: {verilog_metrics['total_correct_connections']}")
                print(f"  Total Generated Connections: {verilog_metrics['total_generated_connections']}")
                print(f"  Total Reference Connections: {verilog_metrics['total_reference_connections']}")
                
                print(f"\nTotal Average Score: {avg_total_score:.2f}/100")

    async def run(self):
        """Main execution method"""
        print(f"Loaded {len(self._load_jsonl())} items for evaluation")
        print(f"Using these prompt keys: {self.config.prompt_keys}")
        print(f"Each prompt will run {self.config.runs_per_prompt} times")
        
        # Load data
        data = self._load_jsonl()
        
        # Calculate total samples to process
        total_samples_to_process = len(data) * len(self.config.prompt_keys)
        
        # Initialize statistics
        stats = {
            "start_time": time.time(),
            "total_samples": 0,
            "errors": []
        }
        
        # Create async session
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Initialize progress bar
            with tqdm(total=total_samples_to_process, desc="Evaluation Progress") as progress_bar:
                
                # Process each item with each prompt
                for item in data:
                    for prompt_key in self.config.prompt_keys:
                        try:
                            # Process this item with this prompt
                            item_results = await self._process_item_with_multiple_runs(session, item, prompt_key)
                            
                            # Update statistics
                            stats["total_samples"] += 1
                            self.results_stats["total_samples"] += 1
                            
                            # Update progress bar with Verilog-A metrics
                            self.update_progress_bar_verilog_a(progress_bar, stats)
                            progress_bar.update(1)
                            
                            # Save results periodically
                            if stats["total_samples"] % 10 == 0:
                                self._update_summary()
                                
                        except Exception as e:
                            error_info = f"Error processing {item.get('img', 'unknown')} with {prompt_key}: {str(e)}\n{traceback.format_exc()}"
                            print(f"\n{error_info}")
                            
                            stats["errors"].append(error_info)
                            self.results_stats["failed_samples"] += 1
                            self.results_stats["total_samples"] += 1
                            
                            # Update progress bar
                            self.update_progress_bar_verilog_a(progress_bar, stats)
                            progress_bar.update(1)
        
        # Save final results
        self.save_results()