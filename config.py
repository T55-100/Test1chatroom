# 服务器配置
class Config:
    DEBUG = True
    SECRET_KEY = 'your-secret-key'
    
# 服务器地址列表，后期可在前端配置
SERVERS = [
    {'name': '本地服务器', 'address': 'http://127.0.0.1:5000'},
    {'name': '服务器1', 'address': 'http://192.168.1.100:5000'},
    {'name': '服务器2', 'address': 'http://192.168.1.101:5000'}
]