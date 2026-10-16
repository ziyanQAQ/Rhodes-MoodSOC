/** 配置 Vitest 从 test/ 发现黑盒测试；运行时可通过文件参数限定验证范围。 */
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
  },
});
