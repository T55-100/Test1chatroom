from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import json
import requests
from config import Config, SERVERS

app = Flask(__name__)
app.config.from_object(Config)

# 初始化SocketIO，使用默认异步模式
socketio = SocketIO(app, cors_allowed_origins="*")

# 存储在线用户信息
online_users = {}

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/chat')
def chat():
    return render_template('chat.html')

@app.route('/api/servers')
def get_servers():
    """获取服务器列表"""
    return jsonify(SERVERS)

@app.route('/api/check_nickname', methods=['POST'])
def check_nickname():
    """检查昵称是否已存在"""
    nickname = request.json.get('nickname')
    is_taken = nickname in online_users.values()
    return jsonify({'taken': is_taken})

@socketio.on('connect')
def handle_connect():
    print('客户端连接')

@socketio.on('disconnect')
def handle_disconnect():
    """处理用户断开连接"""
    sid = request.sid
    if sid in online_users:
        nickname = online_users[sid]
        # 从在线用户列表中移除
        del online_users[sid]
        # 通知其他用户
        emit('user_left', {'nickname': nickname, 'users': list(online_users.values())},
             broadcast=True, include_self=False)
        print(f'{nickname} 已离开聊天室')

@socketio.on('join')
def handle_join(data):
    """处理用户加入聊天室"""
    nickname = data.get('nickname')
    sid = request.sid
    
    # 保存用户信息
    online_users[sid] = nickname
    
    # 加入默认房间
    join_room('chat_room')
    
    # 发送欢迎消息
    emit('welcome', {'message': f'欢迎 {nickname} 加入聊天室！'})
    
    # 通知其他用户有新用户加入
    emit('user_joined', {
        'nickname': nickname,
        'users': list(online_users.values())
    }, broadcast=True, include_self=False)
    
    # 发送当前在线用户列表
    emit('update_users', {'users': list(online_users.values())})
    
    print(f'{nickname} 已加入聊天室')

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

@socketio.on('send_message')
def handle_message(data):
    """处理发送消息"""
    nickname = online_users.get(request.sid)
    message = data.get('message')
    
    # 检查@指令
    if message.startswith('@'):
        parts = message.split(' ', 1)
        command = parts[0]
        
        if command == '@川小农':
            # 处理AI对话指令
            user_question = parts[1] if len(parts) > 1 else "请一字不多也不少地回复:你好，有什么问题吗？请@川小农+问题"
            
            # 先将用户的@川小农消息广播给所有用户
            emit('receive_message', {
                'nickname': nickname,
                'message': message,
                'type': 'text'
            }, broadcast=True)
            
            # 发送一个正在思考的消息给所有用户
            emit('receive_message', {
                'nickname': '川小农',
                'message': '正在思考中...',
                'type': 'ai_response'
            }, broadcast=True)
            
            # 获取AI响应
            response_content = get_ai_response(user_question)
            
            # 发送AI响应给所有用户
            emit('receive_message', {
                'nickname': '川小农',
                'message': response_content,
                'type': 'ai_response'
            }, broadcast=True)
        elif command == '@电影' and len(parts) > 1:
            # 处理电影播放指令
            movie_url = parts[1]
            emit('receive_message', {
                'nickname': nickname,
                'message': f'正在播放电影：{movie_url}',
                'type': 'movie',
                'url': movie_url
            }, broadcast=True)
        else:
            # 处理@用户提醒
            mention_nickname = command[1:]  # 去掉@符号
            emit('receive_message', {
                'nickname': nickname,
                'message': message,
                'type': 'mention',
                'mention': mention_nickname
            }, broadcast=True)
    else:
        # 普通消息
        emit('receive_message', {
            'nickname': nickname,
            'message': message,
            'type': 'text'
        }, broadcast=True)

if __name__ == '__main__':
    # 获取本机IP地址
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))
    local_ip = s.getsockname()[0]
    s.close()
    
    print(f'聊天室服务器启动在：http://{local_ip}:5000 和 http://127.0.0.1:5000')
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)