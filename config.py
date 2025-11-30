# 服务器配置
class Config:
    DEBUG = True
    SECRET_KEY = 'your-secret-key'
    
# 服务器地址列表，后期可在前端配置
SERVERS = [
    {'name': '本地服务器', 'address': 'http://127.0.0.1:5000'},
    {'name': '隧道1', 'address': 'http://103.45.130.80:59043'},
    {'name': '隧道2', 'address': 'http://frp-tag.com:59043'}
]