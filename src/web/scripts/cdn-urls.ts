import { readdir } from 'node:fs/promises';
import { relative, resolve, sep } from 'node:path';
import { argv, stderr, stdout } from 'node:process';
import { pagePaths } from '../src/lib/routes.ts';

export async function cdnUrls(originValue: string, dist: string) {
  if (!originValue || !dist) {
    throw new Error('请设置 CDN_ORIGIN 和 CDN_WEB_DIST');
  }
  const origin = URL.parse(originValue);
  if (
    !origin ||
    (origin.protocol !== 'https:' && origin.protocol !== 'http:') ||
    origin.href !== `${origin.origin}/`
  ) {
    throw new Error('CDN_ORIGIN 必须是前端站点的 HTTP 或 HTTPS 源地址');
  }

  const directory = resolve(dist);
  const entries = await readdir(directory, {
    recursive: true,
    withFileTypes: true,
  });
  const files = entries
    .filter((entry) => entry.isFile())
    .map((entry) => relative(directory, resolve(entry.parentPath, entry.name)));
  if (!files.includes('index.html')) {
    throw new Error('CDN_WEB_DIST 中没有 index.html，请指定前端部署产物目录');
  }
  const assets = files.map(
    (path) => `/${path.split(sep).map(encodeURIComponent).join('/')}`,
  );
  const paths = new Set([
    ...Object.values(pagePaths),
    ...assets,
  ]);
  return [
    ...paths,
  ]
    .sort()
    .map((path) => new URL(path, origin).href);
}

if (import.meta.main) {
  try {
    const urls = await cdnUrls(argv[2], argv[3]);
    stdout.write(`${urls.join('\n')}\n`);
  } catch (error) {
    stderr.write(
      `${error instanceof Error ? error.message : '生成 CDN 刷新清单失败'}\n`,
    );
    process.exitCode = 1;
  }
}
