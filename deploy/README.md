# 部署指南（阿里云轻量应用服务器）

适用环境：Ubuntu 22.04（自带 Python 3.10），2核2G 即可。

两个可独立运行的服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| **WanderWarm Web**（推荐演示用） | 8000 | 原生前端 + SSE 实时 Agent 时间线 |
| Streamlit | 8501 | 备用界面 + Trace Viewer |

## 1. 控制台准备

- 防火墙放行 **TCP 8000**（Web 演示）和 **TCP 8501**（Streamlit，可选）
- 备案说明：用 `http://IP:端口` 访问无需备案；绑域名走 80/443 才需要 ICP 备案

## 2. 服务器初始化

```bash
apt update && apt install -y python3-venv python3-pip git
git clone https://github.com/SayHiToYoung/multi-agent-travel-planner.git
cd multi-agent-travel-planner/python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

## 3. 配置 .env

```bash
cp .env.example .env
nano .env
```

必改三项：

```bash
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-你的key
DEMO_ACCESS_CODE=自定义访问码     # 公网防刷: 输对才能用真实LLM, 务必设置
```

建议同时设 `REPLAN_MODE=agent` 以演示 ReplanAgent。
保护密钥：`chmod 600 .env`

## 4. 手动验证

```bash
streamlit run ui/streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
```

浏览器访问 `http://公网IP:8501`，能出行程即成功，Ctrl+C 退出。

## 5. systemd 常驻

```bash
cp ../deploy/travel-planner-web.service /etc/systemd/system/   # Web 演示 (8000)
cp ../deploy/travel-planner.service /etc/systemd/system/       # Streamlit (8501, 可选)
systemctl daemon-reload
systemctl enable --now travel-planner-web travel-planner
systemctl status travel-planner-web
```

浏览器访问 `http://公网IP:8000` 即为 WanderWarm 演示页。

## 6. 日常更新

```bash
cd /root/multi-agent-travel-planner
git pull
systemctl restart travel-planner
```

## 安全清单

- [ ] `DEMO_ACCESS_CODE` 已设置（否则任何人可消耗你的 LLM 额度）
- [ ] DeepSeek 平台小额充值并设限额
- [ ] `.env` 权限 600，永不提交到 git
- [ ] 演示前 10 分钟自行打开一遍验证
