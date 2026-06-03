# 每日投融资日报自动推送

每天自动从烯牛创投数据获取昨日投融资事件，生成公众号风格报告，发送到指定邮箱。

## 配置 Secrets

仓库 → Settings → Secrets and variables → Actions，添加：

| Secret | 值 |
|--------|---|
| XINIU_API_KEY | FdMow1op4UuhhAHb6DUtFLgNfIakrIQA |
| SMTP_SERVER | smtp.qq.com |
| SMTP_PORT | 465 |
| SMTP_USER | 你的QQ邮箱 |
| SMTP_PASS | QQ邮箱授权码 |
| EMAIL_TO | liuchenghao@xiniudata.com |
