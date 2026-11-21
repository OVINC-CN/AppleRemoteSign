import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, type Page, test } from '@playwright/test';

const backend = 'http://127.0.0.1:18765';
const archive = execFileSync(
  path.resolve('../../.venv/bin/python'),
  [
    '-c',
    'import sys; from tests.archive_helpers import dmg_zip; sys.stdout.buffer.write(dmg_zip(b"browser-upload"))',
  ],
  {
    cwd: path.resolve('../..'),
  },
);
async function startLogin(page: Page, initial = '/jobs') {
  await page.route('**/config.js', (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `window.__APP_CONFIG__ = {API_BASE_UR: '${backend}'};`,
    }),
  );
  await page.goto(initial);
  await page
    .getByRole('link', {
      name: '使用 OIDC 登录',
    })
    .click();
}
async function login(page: Page, username: string, initial = '/jobs') {
  await startLogin(page, initial);
  await page
    .getByRole('button', {
      name: username,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/jobs$/);
  await expect(
    page.getByRole('heading', {
      name: '签名历史',
    }),
  ).toBeVisible();
}

test('parallel login tabs retain their own browser binding', async ({
  page,
  context,
}) => {
  const second = await context.newPage();
  await startLogin(page);
  await startLogin(second);
  for (const current of [
    page,
    second,
  ]) {
    await current
      .getByRole('button', {
        name: 'alice',
        exact: true,
      })
      .click();
    await expect(current).toHaveURL(/\/jobs$/);
    await expect(
      current.getByRole('heading', {
        name: '签名历史',
      }),
    ).toBeVisible();
  }
});

test('login on a local alias reports a missing cookie and allows retry on the API host', async ({
  page,
}) => {
  await page.route('**/config.js', (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `window.__APP_CONFIG__ = {API_BASE_UR: '${backend}'};`,
    }),
  );
  await page.goto('http://localhost:18765/api/v1/auth/login');
  await expect(page).toHaveURL(
    /^http:\/\/127\.0\.0\.1:18765\/test\/authorize\?/,
  );
  await page
    .getByRole('button', {
      name: 'alice',
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/login\?error=login_cookie_missing$/);
  await expect(page.getByRole('alert')).toContainText(
    '浏览器未返回登录校验 Cookie',
  );
  await page
    .getByRole('link', {
      name: '使用 OIDC 登录',
    })
    .click();
  await page
    .getByRole('button', {
      name: 'alice',
      exact: true,
    })
    .click();
  await expect(
    page.getByRole('heading', {
      name: '签名历史',
    }),
  ).toBeVisible();
});

test('missing login cookies show an actionable error', async ({
  page,
  context,
}, testInfo) => {
  await startLogin(page);
  await context.clearCookies();
  await page
    .getByRole('button', {
      name: 'alice',
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/login\?error=login_cookie_missing$/);
  await expect(page.getByRole('alert')).toContainText(
    '浏览器未返回登录校验 Cookie',
  );
  await page.screenshot({
    path: testInfo.outputPath('login.png'),
    fullPage: true,
  });
});

test('administrator configures grants and creates and revokes a one-time key', async ({
  page,
}, testInfo) => {
  await login(page, 'admin');
  await page
    .getByRole('link', {
      name: '应用与权限',
    })
    .click();
  await page
    .getByRole('button', {
      name: '新建应用',
    })
    .click();
  await page
    .getByLabel('应用名称', {
      exact: true,
    })
    .fill('Browser Release');
  await page
    .getByLabel('Identifier', {
      exact: true,
    })
    .fill('com.example.browser');
  const created = page.waitForResponse(
    (response) =>
      response.url().endsWith('/admin/applications') &&
      response.request().method() === 'POST',
  );
  await page
    .getByRole('button', {
      name: '创建应用',
      exact: true,
    })
    .click();
  const application = (await (await created).json()).data;
  await expect(page.getByLabel('显示名称')).toHaveValue('Browser Release');
  await page
    .getByRole('checkbox', {
      name: 'alice',
      exact: true,
    })
    .check();
  await page
    .getByRole('button', {
      name: '保存授权',
    })
    .click();
  await expect(page.getByRole('status')).toContainText('即刻生效');
  await page
    .getByRole('textbox', {
      name: 'Key 名称',
    })
    .fill('Browser CI');
  await page
    .getByRole('button', {
      name: '创建 Key',
      exact: true,
    })
    .click();
  const key = await page.getByTestId('created-key').innerText();
  expect(key).toMatch(/^ars_/);
  await page
    .getByRole('button', {
      name: '已保存，关闭',
    })
    .click();
  await expect(
    page.getByText(key, {
      exact: true,
    }),
  ).toHaveCount(0);
  expect(
    (
      await page.request.get(`${backend}/openapi/v1/openapi.json`, {
        headers: {
          Authorization: `Bearer ${key}`,
        },
      })
    ).status(),
  ).toBe(200);
  await page.reload();
  await page
    .getByRole('button', {
      name: /Browser Release/,
    })
    .click();
  await expect(
    page.getByRole('checkbox', {
      name: 'alice',
      exact: true,
    }),
  ).toBeChecked();
  await expect(
    page.getByText(key, {
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole('button', {
      name: '吊销',
      exact: true,
    })
    .click();
  await page
    .getByRole('button', {
      name: '确认吊销',
    })
    .click();
  await expect(
    page.getByText('已吊销', {
      exact: true,
    }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(`${backend}/openapi/v1/openapi.json`, {
        headers: {
          Authorization: `Bearer ${key}`,
        },
      })
    ).status(),
  ).toBe(401);
  const grants = await page.request.get(
    `${backend}/api/v1/admin/applications/${application.id}/users`,
  );
  expect((await grants.json()).data).toContain('alice');
  await page.screenshot({
    path: testInfo.outputPath('admin.png'),
    fullPage: true,
  });
});

test('member uploads, refreshes, downloads and opens a static deep link', async ({
  page,
}, testInfo) => {
  await login(page, 'alice', '/upload');
  await expect(
    page.getByRole('link', {
      name: '应用与权限',
    }),
  ).toHaveCount(0);
  await page
    .getByRole('link', {
      name: '上传签名',
      exact: true,
    })
    .first()
    .click();
  await page
    .getByRole('combobox', {
      name: '所属应用',
    })
    .click();
  await expect(
    page.getByRole('option', {
      name: /Private/,
    }),
  ).toHaveCount(0);
  await page
    .getByRole('option', {
      name: 'Desktop · com.example.desktop',
    })
    .click();
  await page.getByLabel('选择 ZIP 文件').setInputFiles({
    name: 'BrowserApp-v1.2.3-beta.1.zip',
    mimeType: 'application/zip',
    buffer: archive,
  });
  await page
    .getByRole('button', {
      name: '开始签名',
    })
    .click();
  await expect(
    page.getByRole('heading', {
      name: '任务详情',
    }),
  ).toBeVisible();
  await page
    .getByRole('button', {
      name: '重命名',
      exact: true,
    })
    .click();
  await page
    .getByLabel('任务名称', {
      exact: true,
    })
    .fill('Desktop release 1.2.3');
  await page.screenshot({
    path: testInfo.outputPath('job-rename.png'),
    fullPage: true,
  });
  await page
    .getByRole('button', {
      name: '保存名称',
      exact: true,
    })
    .click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByText('Desktop release 1.2.3', {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole('button', {
      name: '刷新状态',
      exact: true,
    })
    .click();
  await expect(
    page.getByText('已完成', {
      exact: true,
    }),
  ).toBeVisible();
  // Playwright attributes a popup's initial download to its opener page.
  const download = page.waitForEvent('download');
  const linkResponse = page.waitForResponse((response) =>
    response.url().endsWith('/artifact-url'),
  );
  await page
    .getByRole('button', {
      name: '下载产物',
    })
    .click();
  const response = await linkResponse;
  expect(response.status()).toBe(200);
  const downloadUrl = (await response.json()).data.url;
  expect(downloadUrl).toContain('?eo_sign=');
  expect(await response.headerValue('cache-control')).toBe('no-store');
  const artifact = await download;
  expect(artifact.url()).toBe(downloadUrl);
  expect(artifact.suggestedFilename()).toBe('BrowserApp-v1.2.3-beta.1.zip');
  expect(await artifact.failure()).toBeNull();
  await page.reload();
  await expect(
    page.getByRole('heading', {
      name: '任务详情',
    }),
  ).toBeVisible();
  await expect(
    page.getByText('Desktop release 1.2.3', {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('job.png'),
    fullPage: true,
  });
  await page
    .getByRole('link', {
      name: '返回签名历史',
    })
    .click();
  await page
    .getByRole('textbox', {
      name: '搜索任务名或文件名',
    })
    .fill('browserapp');
  await page
    .getByRole('button', {
      name: '搜索',
      exact: true,
    })
    .click();
  await expect(
    page.getByRole('link', {
      name: 'Desktop release 1.2.3',
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole('textbox', {
      name: '搜索任务名或文件名',
    })
    .fill('DESKTOP RELEASE');
  await page
    .getByRole('button', {
      name: '搜索',
      exact: true,
    })
    .click();
  await expect(
    page.getByRole('link', {
      name: 'Desktop release 1.2.3',
      exact: true,
    }),
  ).toBeVisible();
  const filteredUrl = page.url();
  const refreshed = page.waitForResponse(
    (response) =>
      response.request().method() === 'GET' &&
      new URL(response.url()).pathname === '/api/v1/jobs',
  );
  await page
    .getByRole('button', {
      name: '刷新签名历史',
    })
    .click();
  expect((await refreshed).ok()).toBe(true);
  await expect(page).toHaveURL(filteredUrl);
  await expect(
    page.getByRole('link', {
      name: 'Desktop release 1.2.3',
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('history.png'),
    fullPage: true,
  });
  await page
    .getByRole('button', {
      name: 'alice，成员，打开用户菜单',
    })
    .click();
  await page
    .getByRole('menuitem', {
      name: '退出',
      exact: true,
    })
    .click();
  await expect(
    page.getByRole('heading', {
      name: '登录工作台',
    }),
  ).toBeVisible();
});

test('mobile top navigation switches pages and exposes the account menu', async ({
  page,
}) => {
  await page.setViewportSize({
    width: 390,
    height: 844,
  });
  await login(page, 'admin');
  const navigation = page.getByRole('navigation', {
    name: '主导航',
  });
  await expect(navigation).toBeVisible();
  await navigation
    .getByRole('link', {
      name: '应用与权限',
    })
    .click();
  await expect(navigation).toBeVisible();
  await expect(
    page.getByRole('heading', {
      name: '应用与权限',
    }),
  ).toBeVisible();
  await page
    .getByRole('button', {
      name: 'admin，管理员，打开用户菜单',
    })
    .click();
  await page
    .getByRole('menuitem', {
      name: '退出',
      exact: true,
    })
    .click();
  await expect(
    page.getByRole('heading', {
      name: '登录工作台',
    }),
  ).toBeVisible();
});

test('unassigned user sees no applications or jobs', async ({
  page,
}, testInfo) => {
  await login(page, 'bob');
  await expect(
    page.getByText('当前没有获授权的应用，请联系管理员。'),
  ).toBeVisible();
  const result = await page.request.get(`${backend}/api/v1/jobs`);
  expect((await result.json()).data.total).toBe(0);
  expect(
    (await page.request.get(`${backend}/api/v1/admin/users`)).status(),
  ).toBe(403);
  await page.screenshot({
    path: testInfo.outputPath('empty.png'),
    fullPage: true,
  });
});

test('runtime config changes the API destination without rebuilding', async ({
  page,
}) => {
  const alternate = 'https://runtime-api.example';
  const visited: string[] = [];
  await page.route('**/config.js', (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `window.__APP_CONFIG__ = {API_BASE_UR: '${alternate}'};`,
    }),
  );
  await page.route(`${alternate}/**`, (route) => {
    visited.push(route.request().url());
    return route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({
        data: null,
        error_message: 'Please sign in.',
      }),
    });
  });
  await page.goto('/jobs/some-deep-link');
  await expect(
    page.getByRole('link', {
      name: '使用 OIDC 登录',
    }),
  ).toHaveAttribute('href', `${alternate}/api/v1/auth/login`);
  expect(visited).toContain(`${alternate}/api/v1/me`);
});

test('build publishes both client helpers with unchanged contents', async ({
  request,
}) => {
  for (const filename of [
    'sign-and-notarize.sh',
    'archive-helper.py',
  ]) {
    const response = await request.get(`/helper/${filename}`);
    expect(response.status()).toBe(200);
    expect(await response.body()).toEqual(
      readFileSync(path.resolve('../../support-files/scripts', filename)),
    );
  }
});

test('leaving a job aborts its pending refresh and sends no follow-up detail request', async ({
  page,
}) => {
  await login(page, 'alice');
  const id = '11111111-1111-4111-8111-111111111111';
  const job = {
    id,
    status: 'processing',
    filename: 'Pending.zip',
    display_name: 'Pending.zip',
    submission_id: 'apple',
    application: {
      id: 'app',
      name: 'Demo',
      identifier: 'demo',
      enabled: true,
      created_at: '',
    },
    source: '用户 · alice',
    size_bytes: 12,
    created_at: '',
    updated_at: '',
    last_checked_at: null,
    error_message: '',
    diagnostics: '',
    notary_log: null,
    original_sha256: null,
    artifact_sha256: null,
  };
  let details = 0;
  await page.route(`${backend}/api/v1/jobs/${id}`, async (route) => {
    details++;
    await route.fulfill({
      json: {
        data: job,
        error_message: '',
      },
    });
  });
  await page.route(`${backend}/api/v1/jobs/${id}/notary-details`, (route) =>
    route.fulfill({
      json: {
        data: null,
        error_message: '',
      },
    }),
  );
  const refreshUrl = `${backend}/api/v1/jobs/${id}/refresh`;
  const requested = page.waitForRequest(refreshUrl);
  const failed = page.waitForEvent('requestfailed', {
    predicate: (request) => request.url() === refreshUrl,
  });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(refreshUrl, async (route) => {
    await pending;
    await route
      .fulfill({
        json: {
          data: {
            id,
            status: 'processing',
          },
          error_message: '',
        },
      })
      .catch(() => {});
  });
  try {
    await page.goto(`/jobs/${id}`);
    await page
      .getByRole('button', {
        name: '刷新状态',
        exact: true,
      })
      .click();
    await requested;
    await expect(
      page.getByRole('button', {
        name: '刷新状态',
        exact: true,
      }),
    ).toBeDisabled();
    await page
      .getByRole('link', {
        name: '返回签名历史',
      })
      .click();
    await failed;
    await expect(
      page.getByRole('heading', {
        name: '签名历史',
      }),
    ).toBeVisible();
    expect(details).toBe(1);
  } finally {
    release();
  }
});
