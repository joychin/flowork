/** 库构建(设计器插件计划批 C1):npm 可嵌入包(ESM 单文件 + CSS)。
 *
 * 与应用构建(vite.config.ts,index.html 开发态)并存;产物出 dist-lib。
 * react/react-dom/zustand/xyflow 全部内联——宿主零依赖即可挂载,不做外部化
 * (双 react 风险留给正式发包时的 peerDependencies 决策,随开放面批)。
 */

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // React 18 CJS 包裹层的 process.env.NODE_ENV 在 lib 模式下不会被隐式替换——
  // 浏览器里裸 process 直接 ReferenceError;显式 define 钉成 production
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    lib: {
      entry: 'src/embed.tsx',
      name: 'FloworkDesigner',
      formats: ['es'],
      fileName: () => 'flowork-designer.es.js',
    },
    outDir: 'dist-lib',
    emptyOutDir: true,
    copyPublicDir: false,
    sourcemap: false,
    minify: true,
  },
})
