import { act, fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import type { JobDetail, NotaryDetails } from '@/lib/types';
import sample from '@/test-fixtures/notary-accepted.json';
import { JobPage } from './Job';

const { api, notaryApi } = vi.hoisted(() => ({
  api: vi.fn(),
  notaryApi: vi.fn(),
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: (path: string, options?: RequestInit) =>
    path.endsWith('/notary-details')
      ? notaryApi(path, options)
      : api(path, options),
}));
const accepted: NotaryDetails = {
  ...sample,
  status: 'Accepted',
};
const job: JobDetail = {
  id: 'one',
  filename: 'App.zip',
  display_name: 'App.zip',
  status: 'processing',
  application: {
    id: 'app',
    name: 'Demo',
    identifier: 'demo',
    enabled: true,
    created_at: '',
  },
  source: '用户 · alice',
  created_at: '',
  updated_at: '',
  last_checked_at: null,
  size_bytes: 12,
  submission_id: 'apple',
  error_message: '',
  diagnostics: '',
  notary_log: null,
  original_sha256: null,
  artifact_sha256: null,
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
  notaryApi.mockReset().mockResolvedValue(null);
  window.__APP_CONFIG__ = {
    API_BASE_UR: '',
  };
  Object.defineProperty(document, 'visibilityState', {
    value: 'visible',
    configurable: true,
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
async function mount() {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <MemoryRouter
        initialEntries={[
          '/jobs/one',
        ]}
      >
        <Routes>
          <Route
            path="/jobs/:jobId"
            element={<JobPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
  });
  return view;
}

it('keeps the task visible and shows download errors on the current page', async () => {
  api.mockResolvedValueOnce({
    ...job,
    status: 'completed',
  });
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  await mount();
  api.mockRejectedValueOnce(
    new ApiError(410, 'File expired or no longer available.'),
  );
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '下载产物',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('文件已过期或已被删除');
  expect(screen.getByText('已完成')).toBeInTheDocument();
  expect(
    screen.getByRole('heading', {
      name: '任务详情',
    }),
  ).toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
  expect(api).toHaveBeenLastCalledWith(
    '/api/v1/jobs/one/artifact-url',
    expect.anything(),
  );
});

it('renames the task while preserving the original filename and pausing polling', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockResolvedValueOnce({
      ...job,
      display_name: 'Desktop Release',
    });
  await mount();
  fireEvent.click(
    screen.getByRole('button', {
      name: '重命名',
    }),
  );
  expect(screen.getByLabelText('任务名称')).toHaveValue('App.zip');
  fireEvent.change(screen.getByLabelText('任务名称'), {
    target: {
      value: '  Desktop Release  ',
    },
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(api).toHaveBeenCalledTimes(1);
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '保存名称',
      }),
    );
  });
  expect(api).toHaveBeenLastCalledWith(
    '/api/v1/jobs/one',
    expect.objectContaining({
      method: 'PATCH',
      body: JSON.stringify({
        display_name: 'Desktop Release',
      }),
    }),
  );
  expect(screen.getByText('Desktop Release')).toBeInTheDocument();
  expect(
    screen.getByText('App.zip', {
      exact: true,
    }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('keeps a rejected rename open for retry', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockRejectedValueOnce(new ApiError(409, 'Busy'));
  await mount();
  fireEvent.click(
    screen.getByRole('button', {
      name: '重命名',
    }),
  );
  fireEvent.change(screen.getByLabelText('任务名称'), {
    target: {
      value: 'Desktop Release',
    },
  });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '保存名称',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('请稍后重命名');
  expect(screen.getByLabelText('任务名称')).toHaveValue('Desktop Release');
  expect(
    screen.getByRole('button', {
      name: '保存名称',
    }),
  ).toBeEnabled();
  fireEvent.click(
    screen.getByRole('button', {
      name: '取消',
    }),
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(2);
});

it('disables blank names and cancels a pending rename when leaving', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockImplementationOnce(() => new Promise(() => {}));
  const view = await mount();
  fireEvent.click(
    screen.getByRole('button', {
      name: '重命名',
    }),
  );
  fireEvent.change(screen.getByLabelText('任务名称'), {
    target: {
      value: '   ',
    },
  });
  expect(
    screen.getByRole('button', {
      name: '保存名称',
    }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText('任务名称'), {
    target: {
      value: 'Desktop Release',
    },
  });
  fireEvent.click(
    screen.getByRole('button', {
      name: '保存名称',
    }),
  );
  const options = vi.mocked(api).mock.calls[1][1];
  expect(options?.signal?.aborted).toBe(false);
  view.unmount();
  expect(options?.signal?.aborted).toBe(true);
});

it('polls serially and stops at completion', async () => {
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce({
      ...job,
      status: 'completed',
    });
  await mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(api).toHaveBeenCalledTimes(2);
  await act(async () => {
    finish({
      id: 'one',
      status: 'completed',
    });
  });
  expect(screen.getByText('已完成')).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(api).toHaveBeenCalledTimes(3);
});
it('does not poll while the page is hidden', async () => {
  Object.defineProperty(document, 'visibilityState', {
    value: 'hidden',
    configurable: true,
  });
  vi.mocked(api).mockResolvedValue(job);
  await mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(api).toHaveBeenCalledTimes(1);
});
it('shows failed tasks without automatically retrying', async () => {
  vi.mocked(api).mockResolvedValue({
    ...job,
    status: 'failed',
    error_message: 'Signing failed',
  });
  await mount();
  expect(screen.getByRole('alert')).toHaveTextContent('Signing failed');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(api).toHaveBeenCalledTimes(1);
});

it('starts a manual refresh immediately and cancels it when leaving the page', async () => {
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const view = await mount();
  fireEvent.click(
    screen.getByRole('button', {
      name: '刷新状态',
    }),
  );
  expect(api).toHaveBeenCalledTimes(2);
  const [path, options] = vi.mocked(api).mock.calls[1];
  expect(path).toBe('/api/v1/jobs/one/refresh');
  expect(options?.method).toBe('POST');
  expect(options?.signal?.aborted).toBe(false);
  expect(
    screen.getByRole('button', {
      name: '刷新状态',
    }),
  ).toBeDisabled();
  view.unmount();
  expect(options?.signal?.aborted).toBe(true);
  await act(async () => {
    finish({
      id: 'one',
      status: 'processing',
    });
  });
  expect(api).toHaveBeenCalledTimes(2);
});

it('shows a failed refresh immediately even when reloading details is slow', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockRejectedValueOnce(
      new ApiError(500, 'S3 upload failed', {
        id: 'one',
        status: 'failed',
        submission_id: 'apple',
      }),
    )
    .mockImplementationOnce(() => new Promise(() => {}));
  await mount();
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '刷新状态',
      }),
    );
  });
  expect(
    screen.getByText('失败', {
      exact: true,
    }),
  ).toBeInTheDocument();
  expect(
    screen.queryByText('处理中', {
      exact: true,
    }),
  ).not.toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(api).toHaveBeenCalledTimes(3);
});

it('pauses polling after a network error until the user refreshes', async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(job)
    .mockRejectedValueOnce(new Error('请求超时'));
  await mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByRole('alert')).toHaveTextContent('请求超时');
  expect(
    screen.getByRole('button', {
      name: '刷新状态',
    }),
  ).toBeEnabled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(api).toHaveBeenCalledTimes(2);
  vi.mocked(api)
    .mockResolvedValueOnce({
      id: job.id,
      status: 'completed',
    })
    .mockResolvedValueOnce({
      ...job,
      status: 'completed',
    });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '刷新状态',
      }),
    );
  });
  expect(screen.getByText('已完成')).toBeInTheDocument();
});

it('loads the topology above logs and stops fetching details after success', async () => {
  const cachedLog = JSON.stringify(accepted, null, 2);
  api.mockResolvedValueOnce({
    ...job,
    diagnostics: 'Signing completed',
    notary_log: cachedLog,
  });
  notaryApi.mockResolvedValue(accepted);
  const view = await mount();
  const graph = screen.getByRole('group', {
    name: '签名文件拓扑',
  });
  expect(
    graph.compareDocumentPosition(screen.getByText('处理日志')) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByLabelText('签名详情')).toBeInTheDocument();
  expect(screen.getByText('Signing completed')).toBeInTheDocument();
  expect(screen.queryByText('Apple 公证日志')).not.toBeInTheDocument();
  expect(screen.queryByText(/"ticketContents":/)).not.toBeInTheDocument();
  expect(notaryApi).toHaveBeenCalledWith(
    '/api/v1/jobs/one/notary-details',
    expect.objectContaining({
      signal: expect.any(AbortSignal),
    }),
  );
  api
    .mockResolvedValueOnce({
      id: job.id,
      status: 'completed',
    })
    .mockResolvedValueOnce({
      ...job,
      status: 'completed',
    });
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '刷新状态',
      }),
    );
  });
  expect(notaryApi).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole('group', {
      name: '签名文件拓扑',
    }),
  ).toBeInTheDocument();
  view.unmount();
});

it('requests missing details again after the normal status refresh', async () => {
  api
    .mockResolvedValueOnce(job)
    .mockResolvedValueOnce({
      id: job.id,
      status: 'completed',
    })
    .mockResolvedValueOnce({
      ...job,
      status: 'completed',
    });
  notaryApi.mockResolvedValueOnce(null).mockResolvedValueOnce(accepted);
  await mount();
  expect(
    screen.queryByRole('group', {
      name: '签名文件拓扑',
    }),
  ).not.toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(notaryApi).toHaveBeenCalledTimes(2);
  expect(screen.getByText('已完成')).toBeInTheDocument();
  expect(
    screen.getByRole('group', {
      name: '签名文件拓扑',
    }),
  ).toBeInTheDocument();
});

it('keeps detail fetching serial with status refreshes', async () => {
  let finish: (value: null) => void = () => {};
  api.mockResolvedValue(job);
  notaryApi.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await mount();
  expect(
    screen.getByRole('button', {
      name: '刷新状态',
    }),
  ).toBeDisabled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(api).toHaveBeenCalledTimes(1);
  expect(notaryApi).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish(null);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api).toHaveBeenCalledTimes(3);
  expect(notaryApi).toHaveBeenCalledTimes(2);
});

it('allows a detail-only retry without changing the completed job', async () => {
  api.mockResolvedValue({
    ...job,
    status: 'completed',
  });
  notaryApi
    .mockRejectedValueOnce(new ApiError(502, 'Apple log unavailable'))
    .mockResolvedValueOnce(accepted);
  await mount();
  expect(screen.getByRole('alert')).toHaveTextContent('Apple log unavailable');
  expect(screen.getByText('已完成')).toBeInTheDocument();
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '重试获取详情',
      }),
    );
  });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(
    screen.getByRole('group', {
      name: '签名文件拓扑',
    }),
  ).toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(1);
  expect(notaryApi).toHaveBeenCalledTimes(2);
});

it('continues status polling after a detail-only failure', async () => {
  api.mockResolvedValue(job);
  notaryApi.mockRejectedValueOnce(new ApiError(502, 'Temporary log failure'));
  await mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api).toHaveBeenCalledTimes(3);
  expect(notaryApi).toHaveBeenCalledTimes(2);
});

it('does not fetch notarization details before submission', async () => {
  api.mockResolvedValue({
    ...job,
    submission_id: null,
  });
  await mount();
  expect(notaryApi).not.toHaveBeenCalled();
  expect(screen.queryByLabelText('签名详情')).not.toBeInTheDocument();
});

it('cancels a pending details request when leaving', async () => {
  api.mockResolvedValue(job);
  notaryApi.mockImplementation(() => new Promise(() => {}));
  const view = await mount();
  const options = notaryApi.mock.calls[0][1];
  expect(options.signal.aborted).toBe(false);
  view.unmount();
  expect(options.signal.aborted).toBe(true);
});

it('discards results from a previous job after navigation', async () => {
  let finish: (value: NotaryDetails) => void = () => {};
  api.mockResolvedValueOnce(job).mockResolvedValueOnce({
    ...job,
    id: 'two',
    display_name: 'Other job',
  });
  notaryApi
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(null);
  await act(async () => {
    render(
      <MemoryRouter
        initialEntries={[
          '/jobs/one',
        ]}
      >
        <Link to="/jobs/two">另一个任务</Link>
        <Routes>
          <Route
            path="/jobs/:jobId"
            element={<JobPage />}
          />
        </Routes>
      </MemoryRouter>,
    );
  });
  const previous = notaryApi.mock.calls[0][1].signal;
  await act(async () => {
    fireEvent.click(screen.getByText('另一个任务'));
  });
  expect(previous.aborted).toBe(true);
  await act(async () => {
    finish(accepted);
  });
  expect(screen.getByText('Other job')).toBeInTheDocument();
  expect(
    screen.queryByRole('group', {
      name: '签名文件拓扑',
    }),
  ).not.toBeInTheDocument();
  expect(notaryApi.mock.calls[1][0]).toBe('/api/v1/jobs/two/notary-details');
});

it('clears job data when access to details has been revoked', async () => {
  api.mockResolvedValue(job);
  notaryApi.mockRejectedValue(new ApiError(404, 'Job not found'));
  await mount();
  expect(screen.getByRole('alert')).toHaveTextContent('Job not found');
  expect(
    screen.queryByText('App.zip', {
      exact: true,
    }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText('签名详情')).not.toBeInTheDocument();
});
