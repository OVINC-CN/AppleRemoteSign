import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, api } from '@/lib/api';
import type { Application, JobPage as PageData } from '@/lib/types';
import { HistoryPage } from './History';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: vi.fn(),
}));

const application: Application = {
  id: 'app',
  name: 'Desktop',
  identifier: 'com.example.desktop',
  enabled: true,
  created_at: '2026-10-15T08:00:00Z',
};
const history: PageData = {
  items: [
    {
      id: 'one',
      application,
      display_name: 'App-v1.2.3.zip',
      filename: 'App-v1.2.3.zip',
      status: 'processing',
      source: '用户 · alice',
      size_bytes: 1024,
      submission_id: 'apple',
      error_message: '',
      created_at: '2026-10-15T08:00:00Z',
      updated_at: '2026-10-15T08:00:00Z',
      last_checked_at: null,
    },
  ],
  total: 21,
  page: 2,
  page_size: 20,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-15T08:01:05Z'));
  vi.mocked(api).mockReset();
  window.__APP_CONFIG__ = {
    API_BASE_UR: '',
  };
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function mount(data: PageData = history) {
  vi.mocked(api)
    .mockResolvedValueOnce([
      application,
    ])
    .mockResolvedValueOnce(data);
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <MemoryRouter
        initialEntries={[
          '/jobs?application_id=app&status=processing&filename=App&page=2',
        ]}
      >
        <HistoryPage />
      </MemoryRouter>,
    );
  });
  return view;
}

it('keeps history rows visible and shows download errors on the current page', async () => {
  const open = vi.spyOn(window, 'open').mockReturnValue(null);
  await mount({
    ...history,
    items: [
      {
        ...history.items[0],
        status: 'completed',
      },
    ],
  });
  vi.mocked(api).mockRejectedValueOnce(
    new ApiError(410, 'File expired or no longer available.'),
  );
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '下载 App-v1.2.3.zip',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('文件已过期或已被删除');
  expect(
    screen.getByRole('link', {
      name: 'App-v1.2.3.zip',
    }),
  ).toBeInTheDocument();
  expect(screen.getByText('第 2 页')).toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
  expect(api).toHaveBeenLastCalledWith(
    '/api/v1/jobs/one/artifact-url',
    expect.anything(),
  );
});

it.each([
  'preparing',
  'processing',
] as const)(
  'updates elapsed time for %s jobs while completed and failed durations stay fixed',
  async (status) => {
    await mount({
      ...history,
      items: [
        {
          ...history.items[0],
          status,
        },
        {
          ...history.items[0],
          id: 'completed',
          display_name: 'Completed release',
          status: 'completed',
          updated_at: '2026-10-15T08:00:42Z',
        },
        {
          ...history.items[0],
          id: 'failed',
          display_name: 'Failed release',
          status: 'failed',
          updated_at: '2026-10-15T08:00:25Z',
        },
      ],
    });
    expect(
      screen.getByRole('columnheader', {
        name: '耗时',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('cell', {
        name: '00:01:05',
      }),
    ).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      screen.getByRole('cell', {
        name: '00:01:07',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('cell', {
        name: '00:00:42',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('cell', {
        name: '00:00:25',
      }),
    ).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(2);
  },
);

it.each([
  'completed',
  'failed',
] as const)(
  'displays a static duration without a timer for %s jobs',
  async (status) => {
    await mount({
      ...history,
      items: [
        {
          ...history.items[0],
          status,
          updated_at: '2026-10-15T08:00:37Z',
        },
      ],
    });
    expect(
      screen.getByRole('cell', {
        name: '00:00:37',
      }),
    ).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(
      screen.getByRole('cell', {
        name: '00:00:37',
      }),
    ).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(2);
  },
);

it.each([
  'completed',
  'failed',
] as const)(
  'stops counting when a refresh changes the job to %s',
  async (status) => {
    await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      screen.getByRole('cell', {
        name: '00:01:07',
      }),
    ).toBeInTheDocument();
    vi.mocked(api).mockResolvedValueOnce({
      ...history,
      items: [
        {
          ...history.items[0],
          status,
          updated_at: '2026-10-15T08:01:06Z',
        },
      ],
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', {
          name: '刷新签名历史',
        }),
      );
    });
    expect(
      screen.getByRole('cell', {
        name: '00:01:06',
      }),
    ).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(
      screen.getByRole('cell', {
        name: '00:01:06',
      }),
    ).toBeInTheDocument();
    expect(api).toHaveBeenCalledTimes(3);
  },
);

it('counts correctly across an hour boundary', async () => {
  vi.setSystemTime(new Date('2026-10-15T08:59:59Z'));
  await mount();
  expect(
    screen.getByRole('cell', {
      name: '00:59:59',
    }),
  ).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(
    screen.getByRole('cell', {
      name: '01:00:01',
    }),
  ).toBeInTheDocument();
});

it('clamps elapsed time to zero when the browser clock is behind the creation time', async () => {
  await mount({
    ...history,
    items: [
      {
        ...history.items[0],
        created_at: '2026-10-15T08:01:10Z',
      },
    ],
  });
  expect(
    screen.getByRole('cell', {
      name: '00:00:00',
    }),
  ).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(
    screen.getByRole('cell', {
      name: '00:00:01',
    }),
  ).toBeInTheDocument();
});

it('clears the elapsed-time timer when leaving the page', async () => {
  const view = await mount();
  expect(vi.getTimerCount()).toBe(1);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

it('refreshes the same filtered page without hiding rows or duplicating requests', async () => {
  await mount();
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const refresh = screen.getByRole('button', {
    name: '刷新签名历史',
  });
  fireEvent.click(refresh);
  expect(refresh).toBeDisabled();
  expect(refresh).toHaveTextContent('刷新');
  expect(refresh.querySelector('svg')).toHaveClass('animate-spin');
  expect(
    screen.getByRole('link', {
      name: 'App-v1.2.3.zip',
    }),
  ).toBeInTheDocument();
  fireEvent.click(refresh);
  expect(api).toHaveBeenCalledTimes(3);
  expect(vi.mocked(api).mock.calls[2][0]).toBe(vi.mocked(api).mock.calls[1][0]);
  expect(screen.getByLabelText('筛选应用')).toHaveTextContent('Desktop');
  expect(screen.getByLabelText('筛选状态')).toHaveTextContent('处理中');
  expect(screen.getByText('第 2 页')).toBeInTheDocument();
  await act(async () => {
    finish({
      ...history,
      items: [
        {
          ...history.items[0],
          display_name: 'Desktop release',
        },
      ],
    });
  });
  expect(refresh).toBeEnabled();
  expect(
    screen.getByRole('link', {
      name: 'Desktop release',
    }),
  ).toBeInTheDocument();
});

it('keeps the last successful rows after a refresh failure and allows retry', async () => {
  await mount();
  vi.mocked(api).mockRejectedValueOnce(new Error('连接断开'));
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '刷新签名历史',
      }),
    );
  });
  expect(screen.getByRole('alert')).toHaveTextContent('连接断开');
  expect(
    screen.getByRole('link', {
      name: 'App-v1.2.3.zip',
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', {
      name: '刷新签名历史',
    }),
  ).toBeEnabled();
  vi.mocked(api).mockResolvedValueOnce(history);
  await act(async () => {
    fireEvent.click(
      screen.getByRole('button', {
        name: '刷新签名历史',
      }),
    );
  });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(4);
});

it('aborts an obsolete refresh when filters change and ignores its late response', async () => {
  const view = await mount();
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(
    screen.getByRole('button', {
      name: '刷新签名历史',
    }),
  );
  const refreshSignal = vi.mocked(api).mock.calls[2][1]?.signal;
  expect(refreshSignal?.aborted).toBe(false);
  vi.mocked(api).mockResolvedValueOnce({
    ...history,
    page: 1,
    total: 1,
    items: [
      {
        ...history.items[0],
        id: 'failed',
        status: 'failed',
        display_name: 'Failed release',
      },
    ],
  });
  await act(async () => {
    fireEvent.keyDown(
      screen.getByRole('combobox', {
        name: '筛选状态',
      }),
      {
        key: 'Enter',
      },
    );
  });
  await act(async () => {
    fireEvent.keyDown(
      screen.getByRole('option', {
        name: '失败',
      }),
      {
        key: 'Enter',
      },
    );
  });
  expect(refreshSignal?.aborted).toBe(true);
  const [path, options] = vi.mocked(api).mock.calls[3];
  const query = new URL(path, 'http://localhost').searchParams;
  expect(query.get('status')).toBe('failed');
  expect(query.get('page')).toBe('1');
  await act(async () => {
    finish(history);
  });
  expect(
    screen.getByRole('link', {
      name: 'Failed release',
    }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('link', {
      name: 'App-v1.2.3.zip',
    }),
  ).not.toBeInTheDocument();
  view.unmount();
  expect(options?.signal?.aborted).toBe(true);
  expect(api).toHaveBeenCalledTimes(4);
});
