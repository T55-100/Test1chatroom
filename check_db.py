import sqlite3

# 连接到SQLite数据库
conn = sqlite3.connect('users.db')
c = conn.cursor()

# 查询所有表
c.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = c.fetchall()
print('Tables:', tables)

# 查看每个表的结构和数据
for table in tables:
    table_name = table[0]
    print(f'\nTable: {table_name}')
    
    # 查询表结构
    c.execute(f'PRAGMA table_info({table_name})')
    columns = c.fetchall()
    print('Columns:', columns)
    
    # 查询表数据（前5行）
    c.execute(f'SELECT * FROM {table_name} LIMIT 5')
    data = c.fetchall()
    print('Data:', data)

# 关闭连接
conn.close()