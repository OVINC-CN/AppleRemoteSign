import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { NotaryDetails } from '@/lib/types';
import sample from '@/test-fixtures/notary-accepted.json';
import { NotaryDetailsCard } from './NotaryDetailsCard';

const details: NotaryDetails = {
  ...sample,
  status: 'Accepted',
};
function card(value: NotaryDetails) {
  return (
    <NotaryDetailsCard
      details={value}
      loading={false}
      error=""
      disabled={false}
      onRetry={vi.fn()}
    />
  );
}

it('shows accepted metadata and selects a duplicate executable signature', () => {
  const view = render(card(details));
  expect(screen.getByText('Accepted')).toBeInTheDocument();
  expect(screen.getByText('Ready for distribution')).toBeInTheDocument();
  expect(screen.getByText(sample.sha256)).toBeInTheDocument();
  const graph = screen.getByRole('group', {
    name: '签名文件拓扑',
  });
  expect(within(graph).getAllByRole('button')).toHaveLength(6);
  expect(view.container.querySelectorAll('svg path[data-source]')).toHaveLength(
    5,
  );
  fireEvent.click(
    within(graph).getByRole('button', {
      name: sample.ticketContents[0].path,
    }),
  );
  const selected = screen.getByRole('region', {
    name: '节点签名详情',
  });
  expect(within(selected).getByText('arm64')).toBeInTheDocument();
  expect(
    within(selected).getByText(sample.ticketContents[0].cdhash),
  ).toBeInTheDocument();
  expect(within(selected).getByText('相同记录出现 2 次')).toBeInTheDocument();
});

it('supports keyboard selection and shows every architecture on a path', async () => {
  const user = userEvent.setup();
  render(
    card({
      ...details,
      ticketContents: [
        ...sample.ticketContents,
        {
          ...sample.ticketContents[0],
          arch: 'x86_64',
          cdhash: 'intel-hash',
        },
      ],
    }),
  );
  const node = within(
    screen.getByRole('group', {
      name: '签名文件拓扑',
    }),
  ).getByRole('button', {
    name: sample.ticketContents[0].path,
  });
  node.focus();
  await user.keyboard('{Enter}');
  expect(node).toHaveAttribute('aria-pressed', 'true');
  const selected = screen.getByRole('region', {
    name: '节点签名详情',
  });
  expect(within(selected).getByText('arm64')).toBeInTheDocument();
  expect(within(selected).getByText('x86_64')).toBeInTheDocument();
});

it('shows warnings even when notarization succeeded and handles empty tickets', () => {
  render(
    card({
      ...details,
      ticketContents: [],
      issues: [
        {
          severity: 'warning',
          code: 1,
          path: 'App.app',
          architecture: 'arm64',
          message: 'A warning to review',
          docUrl: null,
        },
      ],
    }),
  );
  expect(screen.getByText(/A warning to review/)).toBeInTheDocument();
  expect(
    screen.getByText('公证已通过，Apple 未提供签名票据内容。'),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('group', {
      name: '签名文件拓扑',
    }),
  ).not.toBeInTheDocument();
});

it('hides the card until accepted details are available', () => {
  render(
    <NotaryDetailsCard
      details={null}
      loading={false}
      error=""
      disabled={false}
      onRetry={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText('签名详情')).not.toBeInTheDocument();
});
