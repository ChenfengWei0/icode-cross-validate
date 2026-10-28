# 第二步：依据需求实现

需求条目集：

$items

其他说明：$task

接口已在 $headers 中确定。依据需求条目集实现它们。头文件可以修改，但修改后受影响的要求会重新计划。

不要读取或修改 `$cv/subtasks/`、`$cv/contracts/`，也不要改动 `$cv/interface.json` 以外的 `$cv/` 内容。

**完成后**，在 `$cv/manifests/` 下写一份**新的**完成清单（文件名用递增序号，例如 `001.json`、`002.json`），列出你交付的实现文件（.c）：

```json
{"requirements_id": "$set_id", "files": ["src/account.c"]}
```

没有写新的完成清单，就不会进行检查。
