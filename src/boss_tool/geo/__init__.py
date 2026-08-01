"""P5 地理模块：地址标准化、地理编码、距离计算与缓存。

子模块：
- address_normalizer: 地址标准化
- distance: Haversine 距离与 3km 判断
- geocoder: 高德地图 Web Service API 客户端
- geo_repository: 地理缓存读写、坐标解析、距离计算

约束：
- API Key 不得写入源码/配置/日志/Git
- 一次运行同一地址最多请求一次 API
- 缓存命中不得访问网络
- 不记录 API Key、HTTP Header、Token、Cookie
"""
