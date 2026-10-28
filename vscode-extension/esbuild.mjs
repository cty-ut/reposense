// Two bundles: the extension host (Node) and the webview UI (browser, React).
import * as esbuild from 'esbuild';

const watch = process.argv.includes('--watch');

const builds = [
  {
    entryPoints: ['src/extension.ts'],
    outfile: 'out/extension.js',
    platform: 'node',
    format: 'cjs',
    external: ['vscode'],
  },
  {
    entryPoints: ['src/webview/main.tsx'],
    outfile: 'out/webview.js',
    platform: 'browser',
    format: 'iife',
    jsx: 'automatic',
    loader: { '.css': 'css' },
    minify: !watch,
    define: { 'process.env.NODE_ENV': JSON.stringify(watch ? 'development' : 'production') },
  },
];

await import('fs').then((fs) => fs.rmSync('out', { recursive: true, force: true }));

for (const options of builds) {
  const ctx = await esbuild.context({ bundle: true, sourcemap: true, target: 'es2022', logLevel: 'info', ...options });
  if (watch) {
    await ctx.watch();
  } else {
    await ctx.rebuild();
    await ctx.dispose();
  }
}
