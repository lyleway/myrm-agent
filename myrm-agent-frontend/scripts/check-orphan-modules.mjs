#!/usr/bin/env node
/**
 * 孤儿模块门禁：`src/` 下从产品入口不可达的模块都算孤儿，只要还有一个就失败；没有基线文件、没有豁免标记。
 *
 * 背景：单元测试 import 被测文件，一个从入口不可达的组件它的测试照样绿——测试绿不等于代码活着。
 * 本门禁补上这个结构性盲区；判定口径（入口、可达闭包、barrel、测试排除）见 `orphan-modules-scan.ts`。
 *
 * 处理方式只有两种：接入产品入口；或连同测试、`_ARCH.md` 文档行、i18n 键一起删除。
 * 不提供基线或豁免——存量豁免会让“暂时保留”变成永久保留；一个改动要么接上产品再落地，要么不落地。
 *
 * 反向自检：候选模块数为 0 说明路径或解析器失效，直接失败而不是静默通过。
 *
 * 运行：bun run verify:orphans（`LINT_GATES` 之一，本地 `bun run lint` 与 CI 共用）
 * 退出码：0 = 无孤儿；1 = 发现孤儿或自检失败
 */
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { scanProject } from './orphan-modules-project.mjs';

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function fail(message) {
  console.error(`[orphan-gate] ${message}`);
  process.exit(1);
}

let result;
try {
  result = scanProject(rootDir);
} catch (error) {
  fail(`扫描失败: ${error.message}`);
}

if (result.scanned === 0) {
  fail('自检失败：未在 src/ 扫描到任何模块，路径或扩展名配置可能已失效——拒绝静默通过。');
}

if (result.orphans.length > 0) {
  console.error(`[orphan-gate] ${result.orphans.length} 个孤儿模块（从产品入口不可达）：`);
  for (const orphan of result.orphans) {
    console.error(`    - ${orphan}`);
  }
  fail('\n处理方式：接入产品入口；或连同测试、_ARCH.md 文档行、i18n 键一起删除。没有基线与豁免标记。');
}

console.log(`[orphan-gate] OK：${result.scanned} 个模块均可从产品入口到达，无孤儿。`);
