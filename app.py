from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import json
import requests
import sqlite3
import hashlib
from config import Config, SERVERS

app = Flask(__name__)
app.config.from_object(Config)

# 初始化SocketIO，使用默认异步模式
socketio = SocketIO(app, cors_allowed_origins="*")

# 存储在线用户信息
online_users = {}

# 存储被禁言的用户
muted_users = set()

# 存储历史消息，最多保存100条
message_history = []
MAX_MESSAGE_HISTORY = 100

# 初始化数据库
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

# 创建用户表（如果不存在）
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user'
    )
''')

# 检查是否已有管理员用户
cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
admin = cursor.fetchone()
if not admin:
    # 添加默认管理员用户（密码：admin123，MD5加密）
    admin_password = hashlib.md5('admin123'.encode()).hexdigest()
    cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
                  ('admin', admin_password, 'admin'))
    conn.commit()

# 数据库操作函数
def get_user(username):
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    return cursor.fetchone()

def create_user(username, password, role='user'):
    hashed_password = hashlib.md5(password.encode()).hexdigest()
    try:
        cursor.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', 
                      (username, hashed_password, role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def update_user(username, password=None, role=None):
    if password and role:
        hashed_password = hashlib.md5(password.encode()).hexdigest()
        cursor.execute('UPDATE users SET password = ?, role = ? WHERE username = ?', 
                      (hashed_password, role, username))
    elif password:
        hashed_password = hashlib.md5(password.encode()).hexdigest()
        cursor.execute('UPDATE users SET password = ? WHERE username = ?', 
                      (hashed_password, username))
    elif role:
        cursor.execute('UPDATE users SET role = ? WHERE username = ?', 
                      (role, username))
    conn.commit()

def delete_user(username):
    cursor.execute('DELETE FROM users WHERE username = ?', (username,))
    conn.commit()

def get_all_users():
    cursor.execute('SELECT * FROM users')
    return cursor.fetchall()

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = hashlib.md5(password.encode()).hexdigest()
        
        user = get_user(username)
        if user and user[2] == hashed_password:
            session['username'] = username
            session['role'] = user[3]
            return redirect(url_for('chat'))
        else:
            return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            return render_template('register.html', error='两次输入的密码不一致')
        
        if len(username) < 2 or len(username) > 20:
            return render_template('register.html', error='用户名长度必须在2-20个字符之间')
        
        if len(password) < 6:
            return render_template('register.html', error='密码长度不能少于6个字符')
        
        if create_user(username, password):
            return redirect(url_for('login'))
        else:
            return render_template('register.html', error='用户名已存在')
    return render_template('register.html')

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html', username=session['username'], role=session['role'])

@app.route('/admin')
def admin():
    if 'username' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    users = get_all_users()
    return render_template('admin.html', users=users)

@app.route('/admin/update_user', methods=['POST'])
def update_user_route():
    if 'username' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    username = request.form['username']
    password = request.form['password']
    role = request.form['role']
    
    update_user(username, password if password else None, role)
    return redirect(url_for('admin'))

@app.route('/admin/delete_user', methods=['POST'])
def delete_user_route():
    if 'username' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
    
    username = request.form['username']
    delete_user(username)
    return redirect(url_for('admin'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route('/api/servers')
def get_servers():
    """获取服务器列表"""
    return jsonify(SERVERS)

@app.route('/api/check_nickname', methods=['POST'])
def check_nickname():
    """检查昵称是否已存在"""
    nickname = request.json.get('nickname')
    # 检查用户名是否已被在线用户使用
    is_taken = any(user['username'] == nickname for user in online_users.values())
    return jsonify({'taken': is_taken})

@socketio.on('connect')
def handle_connect():
    print('客户端连接')

@socketio.on('disconnect')
def handle_disconnect():
    """处理用户断开连接"""
    sid = request.sid
    if sid in online_users:
        user_info = online_users[sid]
        username = user_info['username']
        # 从在线用户列表中移除
        del online_users[sid]
        # 准备更新后的用户列表，包含用户名、角色和禁言状态
        users_info = [
            {"username": user["username"], "role": user["role"], "muted": user["muted"]} 
            for user in online_users.values()
        ]
        # 通知其他用户
        emit('user_left', {'nickname': username, 'users': users_info},
             broadcast=True, include_self=False)
        print(f'{username} 已离开聊天室')

@socketio.on('join')
def handle_join(data):
    """处理用户加入聊天室"""
    # 从session获取用户名和角色
    username = session.get('username', data.get('nickname', '匿名用户'))
    role = session.get('role', 'user')
    sid = request.sid
    
    # 保存用户信息（包括角色和禁言状态）
    online_users[sid] = {
        'username': username, 
        'role': role,
        'muted': username in muted_users
    }
    
    # 加入默认房间
    join_room('chat_room')
    
    # 发送欢迎消息
    emit('welcome', {'message': f'欢迎 {username} 加入聊天室！'})
    
    # 准备用户列表，包含用户名、角色和禁言状态
    users_info = [
        {"username": user["username"], "role": user["role"], "muted": user["muted"]} 
        for user in online_users.values()
    ]
    
    # 通知其他用户有新用户加入
    emit('user_joined', {
        'nickname': username,
        'users': users_info
    }, broadcast=True, include_self=False)
    
    # 发送当前在线用户列表
    emit('update_users', {'users': users_info})
    
    # 发送历史消息给新连接的用户
    emit('load_history', {'messages': message_history})
    
    print(f'{username} 已加入聊天室，角色：{role}')

def get_ai_response(user_message):
    """调用SiliconFlow API获取AI响应"""
    api_key = "sk-rjwkexjbigmubjyzwfumnaneidrpummzptrvlhjwzfrpfdeb"
    model_name = "Qwen/Qwen2.5-7B-Instruct"
    api_base = "https://api.siliconflow.cn/v1/"
    
    try:
        url = f"{api_base}chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "你是川小农，一个四川农业大学的智能助手，用中文回答用户问题。"},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7
        }
        
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"大模型调用失败: {e}")
        return f"抱歉，我现在无法回答你的问题。错误信息: {str(e)}"

def get_weather_info(city):
    """调用天气API获取天气信息"""
    url = "https://v2.xxapi.cn/api/weather"
    params = {
        "city": city,
        "key": "5f10e336fa8c0309"
    }
    headers = {
        'User-Agent': 'xiaoxiaoapi/1.0.0'
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        result = response.json()
        return result
    except Exception as e:
        print(f"天气API调用失败: {e}")
        return {"code": 500, "msg": "天气信息获取失败", "data": None}

def search_music(music_name):
    """调用酷狗铃声搜索API获取音乐信息"""
    url = "https://v2.xxapi.cn/api/kugousearch"
    params = {
        "music": music_name
    }
    headers = {
        'User-Agent': 'xiaoxiaoapi/1.0.0'
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        result = response.json()
        return result
    except Exception as e:
        print(f"音乐搜索API调用失败: {e}")
        return {"code": 500, "msg": "音乐搜索失败", "data": None}



@socketio.on('send_message')
def handle_message(data):
    """处理发送消息"""
    user_info = online_users.get(request.sid, {})
    nickname = user_info.get('username', '匿名用户')
    message = data.get('message')
    
    # 检查用户是否被禁言
    if user_info.get('muted'):
        emit('receive_message', {
            'nickname': '系统',
            'message': '您已被管理员禁言，无法发送消息',
            'type': 'text'
        })
        return
    
    # 天气类型定义
    weather_types = {
        '晴': '晴',
        '云': '云',
        '阴': '阴',
        '雨': '雨',
        '雪': '雪',
        '风': '风',
        '雾': '雾',
        '雷': '雷',
        '沙': '沙',
        '未知': '未知'
    }
    
    # 检查@指令
    if message.startswith('@'):
        parts = message.split(' ', 1)
        command = parts[0]
        
        if command == '@川小农':
            # 处理AI对话指令
            user_question = parts[1] if len(parts) > 1 else "请一字不多也不少地回复:你好，有什么问题吗？请@川小农+问题"
            
            # 先将用户的@川小农消息广播给所有用户
            user_msg = {
                'nickname': nickname,
                'message': message,
                'type': 'text'
            }
            emit('receive_message', user_msg, broadcast=True)
            message_history.append(user_msg)
            
            # 发送一个正在思考的消息给所有用户
            thinking_msg = {
                'nickname': '川小农',
                'message': '正在思考中...',
                'type': 'ai_response'
            }
            emit('receive_message', thinking_msg, broadcast=True)
            message_history.append(thinking_msg)
            
            # 获取AI响应
            response_content = get_ai_response(user_question)
            
            # 发送AI响应给所有用户
            ai_msg = {
                'nickname': '川小农',
                'message': response_content,
                'type': 'ai_response'
            }
            emit('receive_message', ai_msg, broadcast=True)
            message_history.append(ai_msg)
        elif command == '@电影' and len(parts) > 1:
            # 处理电影播放指令
            movie_url = parts[1]
            # 拼接解析地址
            parsed_url = f'https://jx.m3u8.tv/jiexi/?url={movie_url}'
            movie_msg = {
                'nickname': nickname,
                'message': f'正在播放电影：{movie_url}',
                'type': 'movie',
                'url': parsed_url
            }
            emit('receive_message', movie_msg, broadcast=True)
            message_history.append(movie_msg)
        elif command == '@天气':
            # 处理天气查询指令
            if len(parts) > 1:
                city = parts[1]
                
                # 处理关闭背景指令
                if city == '关闭':
                    # 发送关闭背景消息
                    close_msg = {
                        'nickname': '系统',
                        'message': '天气背景已关闭',
                        'type': 'weather',
                        'weather_type': 'close',
                        'weather_info': None
                    }
                    emit('receive_message', close_msg, broadcast=True)
                    message_history.append(close_msg)
                    return
                
                # 先将用户的@天气消息广播给所有用户
                user_msg = {
                    'nickname': nickname,
                    'message': message,
                    'type': 'text'
                }
                emit('receive_message', user_msg, broadcast=True)
                message_history.append(user_msg)
                
                # 获取天气信息
                weather_result = get_weather_info(city)
                
                if weather_result.get('code') == 200:
                    weather_data = weather_result.get('data')
                    if weather_data and isinstance(weather_data, dict):
                        # 添加调试日志，查看weather_data的实际结构
                        print(f"天气API返回数据结构: {type(weather_data)}")
                        print(f"天气API返回数据内容: {weather_data}")
                        
                        # 提取城市名称
                        city_name = weather_data.get('city', city)
                        
                        # 获取天气预报数据
                        forecast_list = weather_data.get('data', [])
                        if forecast_list and isinstance(forecast_list, list) and len(forecast_list) > 0:
                            # 获取今日天气
                            today_forecast = forecast_list[1]
                    
                            # 提取今日天气信息
                            weather_desc = today_forecast.get('weather', '未知')
                            temperature = today_forecast.get('temperature', '未知')
                            wind = today_forecast.get('wind', '未知')
                            air_quality = today_forecast.get('air_quality', '未知')
                            
                            # 只显示今日天气信息
                            weather_message = f"{city_name}\n"
                            weather_message += "今日天气：\n"
                            weather_message += f"天气状况：{weather_desc}\n"
                            weather_message += f"温    度：{temperature}\n"
                            weather_message += f"风    力：{wind}\n"
                            weather_message += f"空气质量：{air_quality}\n"

                            # 确定天气类型
                            weather_type = '未知'
                            for key in weather_types.keys():
                                if key != '未知' and key != 'close' and key in weather_desc:
                                    weather_type = key
                                    break
                            
                            # 发送天气消息给所有用户
                            weather_msg = {
                                'nickname': '系统',
                                'message': weather_message,
                                'type': 'weather',
                                'weather_type': weather_type,
                                'weather_info': {
                                    'weather': weather_desc,
                                    'temperature': temperature
                                }
                            }
                            emit('receive_message', weather_msg, broadcast=True)
                            message_history.append(weather_msg)
                        else:
                            # 天气数据格式不正确
                            error_msg = {
                                'nickname': '系统',
                                'message': f"{city} 天气查询失败，未获取到有效预报数据",
                                'type': 'weather',
                                'weather_type': 'unknown',
                                'weather_info': None
                            }
                            emit('receive_message', error_msg, broadcast=True)
                            message_history.append(error_msg)
                    else:
                        # 天气数据为空
                        error_msg = {
                            'nickname': '系统',
                            'message': f"{city} 天气查询失败，返回数据为空",
                            'type': 'weather',
                            'weather_type': 'unknown',
                            'weather_info': None
                        }
                        emit('receive_message', error_msg, broadcast=True)
                        message_history.append(error_msg)
                else:
                    # 天气信息获取失败
                    error_msg = {
                        'nickname': '系统',
                        'message': f"天气信息获取失败：{weather_result.get('msg', '未知错误')}",
                        'type': 'text'
                    }
                    emit('receive_message', error_msg, broadcast=True)
                    message_history.append(error_msg)

        elif command == '@音乐':
            # 处理音乐搜索指令
            if len(parts) > 1:
                music_name = parts[1]
                
                # 先将用户的@音乐消息广播给所有用户
                user_msg = {
                    'nickname': nickname,
                    'message': message,
                    'type': 'text'
                }
                emit('receive_message', user_msg, broadcast=True)
                message_history.append(user_msg)
                
                # 获取音乐搜索结果
                music_result = search_music(music_name)
                
                if music_result.get('code') == 200:
                    music_list = music_result.get('data', [])
                    if music_list:
                        # 发送音乐搜索结果列表
                        music_msg = {
                            'nickname': '系统',
                            'message': f'找到以下关于"{music_name}"的音乐：',
                            'type': 'music_search_result',
                            'music_list': music_list,
                            'search_query': music_name
                        }
                        emit('receive_message', music_msg, broadcast=True)
                        message_history.append(music_msg)
                    else:
                        # 没有找到音乐
                        no_music_msg = {
                            'nickname': '系统',
                            'message': f'没有找到关于"{music_name}"的音乐',
                            'type': 'text'
                        }
                        emit('receive_message', no_music_msg, broadcast=True)
                        message_history.append(no_music_msg)
                else:
                    # 音乐搜索失败
                    error_msg = {
                        'nickname': '系统',
                        'message': f'音乐搜索失败：{music_result.get("msg", "未知错误")}',
                        'type': 'text'
                    }
                    emit('receive_message', error_msg, broadcast=True)
                    message_history.append(error_msg)
            else:
                # 没有提供音乐名称
                help_msg = {
                    'nickname': '系统',
                    'message': '请使用格式：@音乐 音乐名',
                    'type': 'text'
                }
                emit('receive_message', help_msg, broadcast=True)
                message_history.append(help_msg)
        else:
            # 处理@用户提醒
            mention_nickname = command[1:]  # 去掉@符号
            mention_msg = {
                'nickname': nickname,
                'message': message,
                'type': 'mention',
                'mention': mention_nickname
            }
            emit('receive_message', mention_msg, broadcast=True)
            message_history.append(mention_msg)
    else:
        # 普通消息
        normal_msg = {
            'nickname': nickname,
            'message': message,
            'type': 'text'
        }
        emit('receive_message', normal_msg, broadcast=True)
        message_history.append(normal_msg)
    
    # 限制历史消息数量
        if len(message_history) > MAX_MESSAGE_HISTORY:
            message_history.pop(0)

@socketio.on('mute_user')
def handle_mute_user(data):
    """处理禁言用户"""
    username = data.get('username')
    
    # 获取当前用户角色
    current_sid = request.sid
    current_user = online_users.get(current_sid, {})
    current_role = current_user.get('role', 'user')
    
    # 只有管理员可以禁言用户
    if current_role != 'admin':
        return
    
    # 查找目标用户
    target_sid = None
    for sid, user_data in online_users.items():
        if user_data['username'] == username:
            target_sid = sid
            break
    
    if target_sid:
        # 切换禁言状态
        online_users[target_sid]['muted'] = not online_users[target_sid]['muted']
        muted = online_users[target_sid]['muted']
        
        # 更新muted_users集合
        if muted:
            muted_users.add(username)
        else:
            muted_users.discard(username)
        
        # 广播禁言/解除禁言消息
        action = '禁言' if muted else '解除禁言'
        mute_message = {
            'nickname': '系统',
            'message': f'{username} 被管理员{action}',
            'type': 'text'
        }
        emit('receive_message', mute_message, broadcast=True)
        
        # 保存系统消息到历史记录
        message_history.append(mute_message)
        if len(message_history) > MAX_MESSAGE_HISTORY:
            message_history.pop(0)
        
        # 更新在线用户列表
        users_info = [{
            'username': user_data['username'],
            'role': user_data['role'],
            'muted': user_data['muted']
        } for sid, user_data in online_users.items()]
        
        emit('update_users', {'users': users_info}, broadcast=True)
        
        print(f'{username} 被{action}')

@socketio.on('kick_user')
def handle_kick_user(data):
    """处理踢出用户"""
    username = data.get('username')
    
    # 获取当前用户角色
    current_sid = request.sid
    current_user = online_users.get(current_sid, {})
    current_role = current_user.get('role', 'user')
    
    # 只有管理员可以踢出用户
    if current_role != 'admin':
        return
    
    # 查找目标用户
    target_sid = None
    for sid, user_data in online_users.items():
        if user_data['username'] == username:
            target_sid = sid
            break
    
    if target_sid:
        # 广播踢出消息
        kick_message = {
            'nickname': '系统',
            'message': f'{username} 被管理员踢出聊天室',
            'type': 'text'
        }
        emit('receive_message', kick_message, broadcast=True)
        
        # 保存系统消息到历史记录
        message_history.append(kick_message)
        if len(message_history) > MAX_MESSAGE_HISTORY:
            message_history.pop(0)
        
        # 从在线用户列表中移除
        del online_users[target_sid]
        
        # 发送离开消息给被踢用户
        emit('kick_user', {}, room=target_sid)
        
        # 更新在线用户列表
        users_info = [{
            'username': user_data['username'],
            'role': user_data['role'],
            'muted': user_data['muted']
        } for sid, user_data in online_users.items()]
        
        emit('update_users', {'users': users_info}, broadcast=True)
        
        print(f'{username} 被踢出聊天室')

if __name__ == '__main__':
    # 获取本机IP地址
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))
    local_ip = s.getsockname()[0]
    s.close()
    
    print(f'聊天室服务器启动在：http://{local_ip}:5001 和 http://127.0.0.1:5001')
    socketio.run(app, host='0.0.0.0', port=5001, debug=False)