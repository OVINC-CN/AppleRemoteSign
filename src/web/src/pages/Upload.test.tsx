import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError, api, upload } from '@/lib/api';
import { UploadPage } from './Upload';

vi.mock('@/lib/session', () => ({
  useSession: () => ({
    user: {
      max_upload_bytes: 1024,
    },
  }),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: vi.fn(),
  upload: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(api).mockResolvedValue([
    {
      id: 'allowed',
      name: 'Allowed',
      identifier: 'com.app',
      enabled: true,
    },
  ]);
  vi.mocked(upload).mockReset();
});
it('rejects unsupported or oversized files before sending', async () => {
  render(
    <MemoryRouter>
      <UploadPage />
    </MemoryRouter>,
  );
  const input = screen.getByLabelText('选择 ZIP 文件');
  fireEvent.change(input, {
    target: {
      files: [
        new File(
          [
            'test',
          ],
          'App.dmg',
        ),
      ],
    },
  });
  expect(screen.getByRole('alert')).toHaveTextContent('ZIP');
  fireEvent.change(input, {
    target: {
      files: [
        new File(
          [
            new Uint8Array(2048),
          ],
          'App-v1.2.3.zip',
        ),
      ],
    },
  });
  expect(screen.getByRole('alert')).toHaveTextContent('文件大小');
  expect(upload).not.toHaveBeenCalled();
});
it.each([
  'App.zip',
  'App-v1.2.zip',
  'App-v1x2x3.zip',
])('rejects %s without a version before sending', async (filename) => {
  render(
    <MemoryRouter>
      <UploadPage />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText('所属应用')).toHaveValue('allowed'),
  );
  fireEvent.change(screen.getByLabelText('选择 ZIP 文件'), {
    target: {
      files: [
        new File(
          [
            'zip',
          ],
          filename,
        ),
      ],
    },
  });
  expect(screen.getByRole('alert')).toHaveTextContent('版本号');
  expect(
    screen.getByRole('button', {
      name: '开始签名',
    }),
  ).toBeDisabled();
  expect(upload).not.toHaveBeenCalled();
});
it.each([
  'App-1.2.3.zip',
  'App-v1.2.3.zip',
  'App-v1.2.3-alpha.1.zip',
  'App-v1.2.3-beta.2.zip',
  'App-v1.2.3-rc.3.zip',
  'App-v12.34.567-nightly+build.9.ZIP',
])(
  'retains failed task navigation for %s without resubmitting',
  async (filename) => {
    vi.mocked(upload).mockRejectedValue(
      new ApiError(500, 'sign failed', {
        id: 'saved-job',
        status: 'failed',
        submission_id: null,
      }),
    );
    render(
      <MemoryRouter>
        <UploadPage />
      </MemoryRouter>,
    );
    await waitFor(() =>
      expect(screen.getByLabelText('所属应用')).toHaveValue('allowed'),
    );
    fireEvent.change(screen.getByLabelText('选择 ZIP 文件'), {
      target: {
        files: [
          new File(
            [
              'zip',
            ],
            filename,
          ),
        ],
      },
    });
    fireEvent.click(
      screen.getByRole('button', {
        name: '开始签名',
      }),
    );
    expect(
      await screen.findByRole('link', {
        name: '查看已创建任务',
      }),
    ).toHaveAttribute('href', '/jobs/saved-job');
    expect(upload).toHaveBeenCalledTimes(1);
  },
);
