# 仓库同步约定

目标：`https://github.com/keqing111/simulations-innovations`。

本地保留 `origin` 指向 MangoBoost/FAST；`innovations` 指向本研究仓库，并设为默认推送目标。
本机使用 Git Credential Manager / Windows 系统凭据库保存认证，不把 Token 写入源码、
提交、远端 URL 或本文件。令牌失效或由所有者轮换后，需要在凭据库更新一次。
其他克隆不会携带本机认证配置，也不应携带凭据。

## 后续提交

```bash
git status
git diff --check
# 明确选择已核验的源码、脚本和数据；不要盲目强制添加整个 results/。
git add <已确认的文件>
git commit -m "Describe the research change"
git push
```

每次发布前重新运行有关测试，核对实验网格、raw 摘要及派生表来源。
审计JSON/CSV和冻结源码按原始字节保存（部分CSV为CRLF），不要自动格式化或统一换行来破坏历史摘要。
新的完整数据包应有独立名字和校验清单，不覆盖旧包；无效或未完成批次不能混入可信数据。
负性能提升不等于无效数据：来源和计算正确的反例必须保留，并标明适用边界。

上游 AMD/NVIDIA 代码和历史记录为参考实现；本次本机测试覆盖原生解析仿真和实验脚本，
不声称完成 GPU 实机、RCCL/NVSHMEM 或 ns-3 拥塞验证。
