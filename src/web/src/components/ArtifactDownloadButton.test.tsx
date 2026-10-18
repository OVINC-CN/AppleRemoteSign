import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, api } from '@/lib/api';
import { ArtifactDownloadButton } from './ArtifactDownloadButton';
import { ErrorBox } from './shared';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: vi.fn(),
}));

function Download() {
  const [error, setError] = useState('');
  return (
    <>
      <ArtifactDownloadButton
        jobId="one"
        onError={setError}
      />
      <ErrorBox message={error} />
    </>
  );
}

function downloadTab() {
  return {
    opener: window,
  };
}

beforeEach(() => vi.mocked(api).mockReset());
afterEach(() => vi.restoreAllMocks());

it('opens a download tab only after a successful file check and prevents duplicate downloads', async () => {
  const tab = downloadTab();
  const open = vi
    .spyOn(window, 'open')
    .mockReturnValue(tab as unknown as Window);
  let finish: (value: { url: string }) => void = () => {};
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<Download />);
  const button = screen.getByRole('button', {
    name: '下载产物',
  });
  fireEvent.click(button);
  expect(open).not.toHaveBeenCalled();
  expect(api).toHaveBeenCalledWith(
    '/api/v1/jobs/one/artifact-url',
    expect.objectContaining({
      signal: expect.any(AbortSignal),
      cache: 'no-store',
      redirect: 'error',
    }),
  );
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(api).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish({
      url: 'https://cdn.example.com/release.zip?eo_sign=fresh',
    });
  });
  expect(open).toHaveBeenCalledExactlyOnceWith(
    'https://cdn.example.com/release.zip?eo_sign=fresh',
    '_blank',
  );
  expect(tab.opener).toBeNull();
  expect(button).toBeEnabled();
});

it.each([
  [
    new ApiError(410, 'File expired or no longer available.'),
    '文件已过期或已被删除',
  ],
  [
    new ApiError(502, 'S3 artifact lookup failed (ClientError).'),
    'S3 artifact lookup failed',
  ],
  [
    new Error('网络连接失败'),
    '网络连接失败',
  ],
] as const)(
  'displays download errors on the current page without opening or flashing a new tab',
  async (error, message) => {
    const tab = downloadTab();
    const open = vi
      .spyOn(window, 'open')
      .mockReturnValue(tab as unknown as Window);
    vi.mocked(api).mockRejectedValueOnce(error);
    render(<Download />);
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', {
          name: '下载产物',
        }),
      );
    });
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(open).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', {
        name: '下载产物',
      }),
    ).toBeEnabled();
    vi.mocked(api).mockResolvedValueOnce({
      url: 'https://cdn.example.com/release.zip',
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', {
          name: '下载产物',
        }),
      );
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(open).toHaveBeenCalledOnce();
  },
);

it('offers an explicit download link when the browser blocks opening a checked URL', async () => {
  vi.spyOn(window, 'open').mockReturnValue(null);
  vi.mocked(api).mockResolvedValueOnce({
    url: 'https://cdn.example.com/release.zip?eo_sign=fresh',
  });
  render(<Download />);
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '下载产物',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('浏览器拦截了新标签页');
  const link = screen.getByRole('link', {
    name: '打开下载',
  });
  expect(link).toHaveAttribute(
    'href',
    'https://cdn.example.com/release.zip?eo_sign=fresh',
  );
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  expect(api).toHaveBeenCalledOnce();
});

it('cancels pending downloads and never opens a tab after leaving the page', async () => {
  const tab = downloadTab();
  const open = vi
    .spyOn(window, 'open')
    .mockReturnValue(tab as unknown as Window);
  let finish: (value: { url: string }) => void = () => {};
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<Download />);
  fireEvent.click(
    screen.getByRole('button', {
      name: '下载产物',
    }),
  );
  const signal = vi.mocked(api).mock.calls[0][1]?.signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  expect(open).not.toHaveBeenCalled();
  await act(async () => {
    finish({
      url: 'https://cdn.example.com/release.zip',
    });
  });
  expect(open).not.toHaveBeenCalled();
});

it('rejects an invalid download URL and keeps the error on the current page', async () => {
  const tab = downloadTab();
  const open = vi
    .spyOn(window, 'open')
    .mockReturnValue(tab as unknown as Window);
  vi.mocked(api).mockResolvedValueOnce({
    url: 'javascript:alert(1)',
  });
  render(<Download />);
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '下载产物',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent(
    '服务返回了无效的下载地址',
  );
  expect(open).not.toHaveBeenCalled();
});
