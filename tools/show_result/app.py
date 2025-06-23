from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, flash
import os
import glob
import json
from show_html import generate_html_report

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # 用于session加密，请在生产环境中更改

# 默认路径配置
DEFAULT_RESULTS_DIR = '../../benchmark_output/20250623_all_va_data_qwen2.5-vl-32b_v1'
DEFAULT_IMAGE_BASE_DIR = '../../'

def get_results_dir():
    """获取当前配置的结果目录"""
    return session.get('results_dir', DEFAULT_RESULTS_DIR)

def get_image_base_dir():
    """获取当前配置的图片基础目录"""
    return session.get('image_base_dir', DEFAULT_IMAGE_BASE_DIR)

@app.route('/config')
def config():
    """配置页面"""
    return render_template('config.html', 
                         results_dir=get_results_dir(),
                         image_base_dir=get_image_base_dir())

@app.route('/config', methods=['POST'])
def save_config():
    """保存配置"""
    results_dir = request.form.get('results_dir', '').strip()
    image_base_dir = request.form.get('image_base_dir', '').strip()
    
    # 验证路径
    if not results_dir:
        flash('JSON文件路径不能为空', 'error')
        return redirect(url_for('config'))
    
    if not image_base_dir:
        flash('图片路径不能为空', 'error')
        return redirect(url_for('config'))
    
    # 检查JSON目录是否存在
    if not os.path.exists(results_dir):
        flash(f'JSON文件目录不存在: {results_dir}', 'error')
        return redirect(url_for('config'))
    
    # 检查图片目录是否存在
    if not os.path.exists(image_base_dir):
        flash(f'图片目录不存在: {image_base_dir}', 'error')
        return redirect(url_for('config'))
    
    # 保存到session
    session['results_dir'] = results_dir
    session['image_base_dir'] = image_base_dir
    
    flash('配置保存成功！', 'success')
    return redirect(url_for('index'))

@app.route('/image/<path:filename>')
def serve_image(filename):
    """提供图片文件访问"""
    image_dir = os.path.abspath(get_image_base_dir())
    print(f"image_dir: {image_dir}, filename: {filename}")
    return send_from_directory(image_dir, filename)

def get_json_files():
    """获取所有JSON文件并按score倒序排序"""
    results_dir = get_results_dir()
    files = glob.glob(os.path.join(results_dir, '*.json'))
    
    # 读取每个文件的score信息
    files_with_score = []
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 获取score，如果不存在则设为0
                score = data.get('results', {}).get('prompt6', [{}])[0].get('score', 0)
                files_with_score.append((file, score))
        except (json.JSONDecodeError, IndexError, KeyError):
            # 如果读取失败，score设为0
            files_with_score.append((file, 0))
    
    # 按score倒序排序
    files_with_score.sort(key=lambda x: x[1], reverse=True)
    return [file for file, score in files_with_score]

def get_file_index(filename):
    """获取文件在列表中的索引"""
    files = get_json_files()
    try:
        return files.index(filename)
    except ValueError:
        return -1

@app.route('/')
def index():
    """主页，显示所有可用的JSON文件"""
    # 检查是否有配置的路径
    results_dir = get_results_dir()
    if not os.path.exists(results_dir):
        flash(f'JSON文件目录不存在: {results_dir}，请先配置路径', 'error')
        return redirect(url_for('config'))
    
    try:
        files = get_json_files()
        file_list = []
        for file in files:
            basename = os.path.basename(file)
            # 读取score信息用于显示
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    score = data.get('results', {}).get('prompt6', [{}])[0].get('score', 0)
            except:
                score = 0
                
            file_list.append({
                'number': basename.split('_')[0],
                'name': basename,
                'path': basename,  # 使用basename作为路径，供url_for使用
                'score': score
            })
        return render_template('index.html', files=file_list, results_dir=results_dir)
    except Exception as e:
        flash(f'读取文件时出错: {str(e)}', 'error')
        return redirect(url_for('config'))

@app.route('/view/<path:filename>')
def view_file(filename):
    """加载JSON文件，渲染并查看"""
    results_dir = get_results_dir()
    filepath = os.path.join(results_dir, filename)
    if not os.path.exists(filepath):
        flash('文件不存在', 'error')
        return redirect(url_for('index'))
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 获取图片路径
        image_path = None
        if 'image' in data and 'path' in data['image']:
            # 使用Flask的url_for生成图片URL
            image_path = url_for('serve_image', filename=data['image']['path'])
        
        content = generate_html_report(data, image_path=image_path)
        
        files = get_json_files()
        current_index = get_file_index(filepath)
        
        prev_file = files[current_index - 1] if current_index > 0 else None
        next_file = files[current_index + 1] if current_index < len(files) - 1 else None
        
        return render_template('viewer.html',
                             content=content,
                             current_file=filename,
                             prev_file=os.path.basename(prev_file) if prev_file else None,
                             next_file=os.path.basename(next_file) if next_file else None)
    except Exception as e:
        flash(f'处理文件时出错: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/search')
def search():
    """搜索文件"""
    query = request.args.get('query', '').strip()
    if not query:
        return redirect(url_for('index'))
    
    try:
        files = get_json_files()
        results = []
        
        for file in files:
            basename = os.path.basename(file)
            if query in basename:
                # 读取score信息用于显示
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        score = data.get('results', {}).get('prompt6', [{}])[0].get('score', 0)
                except:
                    score = 0
                    
                results.append({
                    'number': basename.split('_')[0],
                    'name': basename,
                    'path': basename,  # 使用basename作为路径
                    'score': score
                })
        
        return render_template('index.html', files=results, search_query=query, results_dir=get_results_dir())
    except Exception as e:
        flash(f'搜索时出错: {str(e)}', 'error')
        return redirect(url_for('config'))

if __name__ == '__main__':
    app.run(debug=True, host='192.168.99.119', port=5000) 