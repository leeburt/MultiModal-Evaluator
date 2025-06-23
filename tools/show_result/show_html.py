import json


def generate_html_report(data, image_name=None, image_path=None):
        """生成HTML格式的评估报告"""
        results = data['results']['prompt6'][0]['verilog_a_analysis']
        if image_name is None:
            image_name = data.get('image', {}).get('file', 'unknown_image')
            
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Verilog-A 评估报告</title>
            <style>
                body {{ 
                    font-family: Arial, sans-serif; 
                    margin: 0;
                    padding: 20px;
                    background-color: #f5f5f5;
                }}
                .container {{ 
                    display: flex;
                    gap: 20px;
                    max-width: 1800px;
                    margin: 0 auto;
                }}
                .left-panel, .right-panel {{
                    flex: 1;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    padding: 20px;
                    height: calc(100vh - 40px);
                    overflow-y: auto;
                }}
                .image-section {{
                    margin-bottom: 20px;
                    padding: 15px;
                    border: 1px solid #e0e0e0;
                    border-radius: 6px;
                    background: white;
                    text-align: center;
                }}
                .image-section .section-title {{
                    font-size: 18px;
                    font-weight: bold;
                    margin-bottom: 15px;
                    color: #333;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 8px;
                }}
                .image-container {{
                    max-width: 100%;
                    overflow: auto;
                }}
                .image-container img {{
                    max-width: 100%;
                    height: auto;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                }}
                .no-image {{
                    color: #666;
                    font-style: italic;
                    padding: 20px;
                }}
                .section {{ 
                    margin-bottom: 20px;
                    padding: 15px;
                    border: 1px solid #e0e0e0;
                    border-radius: 6px;
                    background: white;
                }}
                .section-title {{
                    font-size: 18px;
                    font-weight: bold;
                    margin-bottom: 15px;
                    color: #333;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 8px;
                }}
                .code-block {{ 
                    background-color: #f8f8f8;
                    padding: 12px;
                    border-radius: 4px;
                    font-family: 'Courier New', monospace;
                    font-size: 14px;
                    white-space: pre-wrap;
                    overflow-x: auto;
                    border: 1px solid #e0e0e0;
                }}
                .json-block {{
                    background-color: #f8f8f8;
                    padding: 12px;
                    border-radius: 4px;
                    font-family: 'Courier New', monospace;
                    font-size: 14px;
                    white-space: pre-wrap;
                    overflow-x: auto;
                    border: 1px solid #e0e0e0;
                }}
                .mapping-table {{ 
                    width: 100%;
                    border-collapse: collapse;
                    margin: 10px 0;
                    font-size: 14px;
                }}
                .mapping-table th, .mapping-table td {{ 
                    padding: 8px;
                    border: 1px solid #e0e0e0;
                    text-align: left;
                }}
                .mapping-table th {{ 
                    background-color: #f5f5f5;
                    font-weight: bold;
                }}
                .connection-list {{ 
                    list-style-type: none;
                    padding: 0;
                    margin: 0;
                }}
                .connection-item {{ 
                    padding: 8px;
                    margin: 4px 0;
                    background-color: #f8f8f8;
                    border-radius: 4px;
                    border: 1px solid #e0e0e0;
                    font-size: 14px;
                }}
                .success {{ color: #28a745; }}
                .warning {{ color: #ffc107; }}
                .error {{ color: #dc3545; }}
                .metric-box {{
                    display: inline-block;
                    padding: 8px 15px;
                    margin: 5px;
                    background-color: #f8f8f8;
                    border-radius: 4px;
                    border: 1px solid #e0e0e0;
                    font-size: 14px;
                }}
                .metric-value {{
                    font-weight: bold;
                    margin-right: 5px;
                }}
                .metric-label {{
                    color: #666;
                }}
                .module-mapping-section {{
                    margin-bottom: 20px;
                    padding: 15px;
                    background-color: #f8f8f8;
                    border-radius: 6px;
                    border: 1px solid #e0e0e0;
                }}
                .module-mapping-title {{
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 10px;
                    color: #333;
                }}
                .module-mapping-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 10px;
                    background-color: white;
                }}
                .module-mapping-table th, .module-mapping-table td {{
                    padding: 8px;
                    border: 1px solid #e0e0e0;
                    text-align: left;
                }}
                .module-mapping-table th {{
                    background-color: #f5f5f5;
                    font-weight: bold;
                }}
                .module-mapping-table td {{
                    font-family: 'Courier New', monospace;
                }}
                .connection-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 10px;
                    background-color: white;
                }}
                .connection-table th, .connection-table td {{
                    padding: 8px;
                    border: 1px solid #e0e0e0;
                    text-align: left;
                    font-family: 'Courier New', monospace;
                }}
                .connection-table th {{
                    background-color: #f5f5f5;
                    font-weight: bold;
                }}
                .port-info {{
                    margin-left: 20px;
                    font-size: 13px;
                    color: #666;
                }}
                .port-type {{
                    font-weight: bold;
                    color: #333;
                }}
                .unmatched-modules {{
                    display: flex;
                    gap: 20px;
                }}
                .unmatched-column {{
                    flex: 1;
                }}
                .module-pair {{
                    display: flex;
                    gap: 20px;
                    margin-bottom: 10px;
                }}
                .module-column {{
                    flex: 1;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="left-panel">
                    <!-- 图片显示区域 -->
                    <div class="image-section">
                        <div class="section-title">原始图片</div>
                        {image_content}
                    </div>
                    
                    <div class="section">
                        <div class="section-title">原始代码</div>
                        <h3>标签代码</h3>
                        <div class="code-block">{label_code}</div>
                        <h3>LLM生成代码</h3>
                        <div class="code-block">{llm_code}</div>
                    </div>
                    <div class="section">
                        <div class="section-title">解析结果</div>
                        <h3>标签解析</h3>
                        <div class="json-block">{parsed_label}</div>
                        <h3>LLM解析</h3>
                        <div class="json-block">{parsed_llm}</div>
                    </div>
                </div>
                
                <div class="right-panel">
                    <div class="section">
                        <div class="section-title">评估指标</div>
                        <div class="metric-box">
                            <span class="metric-label">模块映射:</span>
                            <span class="metric-value">P: {module_mappings_precision:.2f}, R: {module_mappings_recall:.2f}, F1: {module_mappings_f1:.2f}</span>
                        </div>
                        <div class="metric-box">
                            <span class="metric-label">端口映射:</span>
                            <span class="metric-value">P: {port_precision:.2f}, R: {port_recall:.2f}, F1: {port_f1:.2f}</span>
                        </div>
                        <div class="metric-box">
                            <span class="metric-label">模块+端口数量匹配:</span>
                            <span class="metric-value">P: {module_precision:.2f}, R: {module_recall:.2f}, F1: {module_f1:.2f}</span>
                        </div>
                        <div class="metric-box">
                            <span class="metric-label">连接匹配:</span>
                            <span class="metric-value">P: {connection_precision:.2f}, R: {connection_recall:.2f}, F1: {connection_f1:.2f}</span>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">模块映射和匹配结果</div>
                        
                        <div class="module-mapping-section">
                            <div class="module-mapping-title">模块映射情况</div>
                            <table class="module-mapping-table">
                                <tr>
                                    <th>标签模块</th>
                                    <th>LLM模块</th>
                                </tr>
                                {module_mappings}
                            </table>
                        </div>

                        <div class="module-mapping-section">
                            <div class="module-mapping-title">模块匹配情况</div>
                            <table class="module-mapping-table">
                                <tr>
                                    <th>标签模块</th>
                                    <th>LLM模块</th>
                                </tr>
                                {module_matches}
                            </table>
                        </div>

                        <div class="module-mapping-section">
                            <div class="module-mapping-title">未匹配的模块</div>
                            <div class="unmatched-modules">
                                <div class="unmatched-column">
                                    <h3>标签模块</h3>
                                    {fn_modules}
                                </div>
                                <div class="unmatched-column">
                                    <h3>LLM模块</h3>
                                    {fp_modules}
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">连接信息</div>
                        <h3>标签连接</h3>
                        <table class="connection-table">
                            <tr>
                                <th>驱动源</th>
                                <th>负载</th>
                            </tr>
                            {label_connections}
                        </table>
                        <h3>LLM连接</h3>
                        <table class="connection-table">
                            <tr>
                                <th>驱动源</th>
                                <th>负载</th>
                            </tr>
                            {llm_connections}
                        </table>
                    </div>

                    <div class="section">
                        <div class="section-title">连接匹配结果</div>
                        <h3>正确匹配的连接</h3>
                        <ul class="connection-list">
                            {matched_connections}
                        </ul>

                        <h3>标签未匹配的连接</h3>
                        <ul class="connection-list">
                            {fn_connections}
                        </ul>

                        <h3>LLM未匹配的连接</h3>
                        <ul class="connection-list">
                            {fp_connections}
                        </ul>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        # 准备图片内容
        if image_path:
            image_content = f'<div class="image-container"><img src="{image_path}" alt="原始图片" /></div>'
        else:
            image_content = '<div class="no-image">图片文件不存在或路径未配置</div>'

        # 准备模块映射表格内容
        module_mappings_html = ""
        for label_mod, llm_mod in results["module_mappings"].items():
            module_mappings_html += f"""
                <tr>
                    <td>{label_mod}</td>
                    <td>{llm_mod}</td>
                </tr>
            """

        # 准备模块匹配表格内容
        module_matches_html = ""
        for match in results["match_modules"]:
            label_mod, llm_mod = match
            module_matches_html += f"""
                <tr>
                    <td>{label_mod}</td>
                    <td>{llm_mod}</td>
                </tr>
            """

        # 准备未匹配和错误匹配的模块列表
        def format_module_with_ports(module_name, parsed_data):
            module_data = parsed_data["modules"].get(module_name, {})
            ports = module_data.get("ports", {})
            return f"""
                <div class='connection-item'>
                    {module_name}
                    <div class='port-info'>
                        <div><span class='port-type'>输入端口:</span> {', '.join(ports.get('inputs', []))}</div>
                        <div><span class='port-type'>输出端口:</span> {', '.join(ports.get('outputs', []))}</div>
                        <div><span class='port-type'>双向端口:</span> {', '.join(ports.get('inouts', []))}</div>
                    </div>
                </div>
            """

        # 创建映射关系字典
        label_to_llm = results["module_mappings"]
        llm_to_label = {v: k for k, v in label_to_llm.items()}

        # 准备未匹配模块的HTML
        def prepare_unmatched_modules_html():
            # 获取所有未匹配的模块
            fn_modules = set(results["FN_modules"])
            fp_modules = set(results["FP_modules"])
            
            # 创建模块对列表
            module_pairs = []
            
            # 1. 处理已映射但未匹配的模块对
            # 创建要移除的模块集合
            to_remove_label = set()
            to_remove_llm = set()
            
            for label_mod in fn_modules:
                if label_mod in label_to_llm:
                    llm_mod = label_to_llm[label_mod]
                    if llm_mod in fp_modules:
                        module_pairs.append((label_mod, llm_mod))
                        to_remove_label.add(label_mod)
                        to_remove_llm.add(llm_mod)
            
            # 移除已处理的模块
            fn_modules -= to_remove_label
            fp_modules -= to_remove_llm
            
            # 2. 处理剩余的未映射模块
            remaining_pairs = []
            for label_mod in sorted(fn_modules):
                remaining_pairs.append((label_mod, None))
            for llm_mod in sorted(fp_modules):
                remaining_pairs.append((None, llm_mod))
            
            # 生成HTML
            html = ""
            for label_mod, llm_mod in module_pairs + remaining_pairs:
                html += "<div class='module-pair'>"
                if label_mod:
                    html += f"<div class='module-column'>{format_module_with_ports(label_mod, results['parsed_label'])}</div>"
                else:
                    html += "<div class='module-column'></div>"
                if llm_mod:
                    html += f"<div class='module-column'>{format_module_with_ports(llm_mod, results['parsed_llm'])}</div>"
                else:
                    html += "<div class='module-column'></div>"
                html += "</div>"
            return html

        fn_modules_html = prepare_unmatched_modules_html()
        fp_modules_html = ""  # 不再需要单独的fp_modules_html，因为已经包含在fn_modules_html中

        # 准备连接列表内容
        def format_connection(conn):
            return f"<li class='connection-item'>{conn[0][0]}.{conn[0][1]} → {conn[1][0]}.{conn[1][1]}</li>"

        def format_connection_table(conn):
            return f"<tr><td>{conn[0][0]}.{conn[0][1]}</td><td>{conn[1][0]}.{conn[1][1]}</td></tr>"

        # 准备连接表格内容
        label_connections_html = "".join(format_connection_table(conn) for conn in results["label_connections"])
        llm_connections_html = "".join(format_connection_table(conn) for conn in results["llm_connections"])
        
        # 准备连接列表内容
        matched_connections_html = "".join(format_connection(conn) for conn in results["matched_connections"])
        fn_connections_html = "".join(format_connection(conn) for conn in results["FN_connections"])
        fp_connections_html = "".join(format_connection(conn) for conn in results["FP_connections"])

        # 格式化JSON输出
        parsed_label_str = json.dumps(results["parsed_label"], indent=2, ensure_ascii=False)
        parsed_llm_str = json.dumps(results["parsed_llm"], indent=2, ensure_ascii=False)

        # 填充模板
        html_content = html_template.format(
            image_name=image_name,
            image_content=image_content,
            module_mappings_f1=results["module_mappings_metrics"]["f1"],
            module_mappings_precision=results["module_mappings_metrics"]["precision"],
            module_mappings_recall=results["module_mappings_metrics"]["recall"],
            port_precision = results["port_metrics"]["precision"],
            port_recall = results["port_metrics"]["recall"],
            port_f1 = results["port_metrics"]["f1"],
            module_f1=results["module_metrics"]["f1"],
            module_precision=results["module_metrics"]["precision"],
            module_recall=results["module_metrics"]["recall"],
            connection_f1=results["connection_metrics"]["f1"],
            connection_precision=results["connection_metrics"]["precision"],
            connection_recall=results["connection_metrics"]["recall"],
            module_mappings=module_mappings_html,
            module_matches=module_matches_html,
            fn_modules=fn_modules_html,
            fp_modules=fp_modules_html,
            label_code=results["label_code_str"],
            llm_code=results["llm_code_str"],
            parsed_label=parsed_label_str,
            parsed_llm=parsed_llm_str,
            label_connections=label_connections_html,
            llm_connections=llm_connections_html,
            matched_connections=matched_connections_html,
            fn_connections=fn_connections_html,
            fp_connections=fp_connections_html
        )

        return html_content