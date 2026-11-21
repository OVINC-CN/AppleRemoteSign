import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { cdnUrls } from '../../scripts/cdn-urls';

const origin = 'https://sign.example.com';

it('lists fixed page URLs and all built files', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'applesign-cdn-'));
  try {
    await mkdir(join(dist, 'assets'));
    await mkdir(join(dist, 'helper'));
    await Promise.all([
      writeFile(join(dist, 'index.html'), ''),
      writeFile(join(dist, 'config.js'), ''),
      writeFile(join(dist, 'assets', 'index-abc.css'), ''),
      writeFile(join(dist, 'assets', 'index-xyz.js'), ''),
      writeFile(join(dist, 'helper', 'file #1.sh'), ''),
    ]);
    expect(await cdnUrls(origin, dist)).toEqual([
      `${origin}/`,
      `${origin}/admin/applications`,
      `${origin}/assets/index-abc.css`,
      `${origin}/assets/index-xyz.js`,
      `${origin}/config.js`,
      `${origin}/helper/file%20%231.sh`,
      `${origin}/index.html`,
      `${origin}/jobs`,
      `${origin}/jobs/detail`,
      `${origin}/login`,
      `${origin}/upload`,
    ]);
  } finally {
    await rm(dist, {
      recursive: true,
      force: true,
    });
  }
});

it('requires a site origin and a build directory containing index.html', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'applesign-cdn-'));
  try {
    await expect(cdnUrls(`${origin}/jobs`, dist)).rejects.toThrow('CDN_ORIGIN');
    await expect(cdnUrls(origin, dist)).rejects.toThrow('index.html');
  } finally {
    await rm(dist, {
      recursive: true,
      force: true,
    });
  }
});
