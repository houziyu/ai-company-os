# 供应商密钥加密存储 — 证据索引

验收报告：[../../provider-secret-storage.md](../../provider-secret-storage.md)
ADR：[ADR-0006](../../../decisions/0006-provider-secret-storage.md)

## db-rows.txt

隔离 Compose 栈（`acos-phase03-test`）中真实保存一行后的数据库状态，取自
`psql`，非测试替身。格式：`id|secret_source|secret_ref is null|密文前缀|指纹`。

要点：

- `secret_source=DB`，`secret_ref` 为 NULL（旧环境变量引用已不再使用）；
- `secret_ciphertext` 以 `v1.` 开头（AES-GCM 封套），明文 `sk-…` 在任何列中不出现；
- 第二次保存（替换密钥）后 `version=2` 且指纹由 `1a…` 变为 `d009674f9fbb…`，
  证明凭据变更会作废旧探测证据。

## 未保存为文件的实测结论

以下项目在真实容器与本机测试库上执行过，未保留原始日志文件；结论与命令见验收报告：

- 后端 `pytest`：258 项通过。
- 前端 `pnpm check`：lint、typecheck、116 项测试、build 全部通过。
- 迁移 `a7d2e5c81b64`：`upgrade` → 插入 DB 行 → `downgrade` 被拒绝 → 清空后
  `downgrade` 成功 → 再 `upgrade` 成功。
- 浏览器：模型中心抽屉的密钥框为 `type=password`、初始为空、
  `autocomplete=new-password`；留空保存后密文与版本不变；填写新密钥保存后
  DOM 与页面文本均不含该值；390×844 下抽屉不溢出、输入高 44px、字号 16px；
  文字对比度 17.85:1、控件边框 4.76:1。

## 未执行

- 截图：`tab.screenshot()` 在本机 IAB 上连续两次 30 秒超时（此前会话已记录该
  运行时限制），因此本轮**没有**截图文件，视觉检查以 DOM 快照与计算样式完成。
  这低于 `docs/quality-gates.md` 的截图要求，按"部分完成"记录。
- 200% 浏览器缩放：此前已确认本机不可真实模拟，未重复尝试。
- 真实 DeepSeek 调用：无可用密钥，未执行。
