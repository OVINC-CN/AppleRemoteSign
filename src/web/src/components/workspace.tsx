import {
  ChevronDown,
  ChevronRight,
  History,
  KeyRound,
  LogOut,
  UploadCloud,
} from 'lucide-react';
import { DropdownMenu } from 'radix-ui';
import { useState } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation } from 'react-router-dom';
import { ErrorBox } from '@/components/shared';
import { ButtonIcon } from '@/components/ui/button';
import { errorMessage } from '@/lib/api';
import { useSession } from '@/lib/session';
import type { Me } from '@/lib/types';

const navigation = [
  {
    to: '/jobs',
    icon: History,
    label: '签名历史',
  },
  {
    to: '/upload',
    icon: UploadCloud,
    label: '上传签名',
  },
  {
    to: '/admin/applications',
    icon: KeyRound,
    label: '应用与权限',
  },
];

export function Brand() {
  return (
    <Link
      to="/jobs"
      className="inline-flex h-8 shrink-0 items-center gap-2 rounded-sm text-[#1d1d1f] transition-opacity hover:opacity-75 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
    >
      <svg
        aria-hidden="true"
        focusable="false"
        viewBox="0 12 14 17"
        fill="currentColor"
        className="h-[22px] w-[18px] shrink-0"
      >
        <path d="m13.0729 17.6825a3.61 3.61 0 0 0 -1.7248 3.0365 3.5132 3.5132 0 0 0 2.1379 3.2223 8.394 8.394 0 0 1 -1.0948 2.2618c-.6816.9812-1.3943 1.9623-2.4787 1.9623s-1.3633-.63-2.613-.63c-1.2187 0-1.6525.6507-2.644.6507s-1.6834-.9089-2.4787-2.0243a9.7842 9.7842 0 0 1 -1.6628-5.2776c0-3.0984 2.014-4.7405 3.9969-4.7405 1.0535 0 1.9314.6919 2.5924.6919.63 0 1.6112-.7333 2.8092-.7333a3.7579 3.7579 0 0 1 3.1604 1.5802zm-3.7284-2.8918a3.5615 3.5615 0 0 0 .8469-2.22 1.5353 1.5353 0 0 0 -.031-.32 3.5686 3.5686 0 0 0 -2.3445 1.2084 3.4629 3.4629 0 0 0 -.8779 2.1585 1.419 1.419 0 0 0 .031.2892 1.19 1.19 0 0 0 .2169.0207 3.0935 3.0935 0 0 0 2.1586-1.1368z" />
      </svg>
      <span className="whitespace-nowrap font-sans text-[18px] font-medium leading-none tracking-[-0.025em]">
        Apple Sign
      </span>
    </Link>
  );
}

function AccountMenu({
  user,
  signingOut,
  onLogout,
}: {
  user: Me;
  signingOut: boolean;
  onLogout: () => void;
}) {
  const role = user.is_admin ? '管理员' : '成员';
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          disabled={signingOut}
          aria-busy={signingOut || undefined}
          aria-label={`${user.username}，${role}，打开用户菜单`}
          className="flex h-8 shrink-0 items-center gap-2 rounded-md px-2 outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/30 disabled:opacity-50"
        >
          <span
            aria-hidden="true"
            className="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-medium text-foreground"
          >
            {user.username.slice(0, 1).toUpperCase()}
          </span>
          <span className="hidden max-w-32 truncate text-[13px] font-medium sm:block">
            {user.username}
          </span>
          <span className="hidden text-xs text-muted-foreground md:block">
            {role}
          </span>
          <ButtonIcon
            icon={ChevronDown}
            loading={signingOut}
            className="size-3.5 shrink-0 text-muted-foreground"
          />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="bottom"
          align="end"
          sideOffset={8}
          className="z-50 min-w-48 rounded-lg border bg-popover p-1 text-[13px] shadow-md outline-none"
        >
          <DropdownMenu.Item
            disabled={signingOut}
            onSelect={onLogout}
            className="flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 outline-none focus:bg-accent data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
          >
            <LogOut
              aria-hidden="true"
              className="size-4"
            />
            退出
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function WorkspaceLayout() {
  const { user, logout } = useSession();
  const { pathname } = useLocation();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState('');
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
      />
    );
  }

  async function signOut() {
    if (signingOut) {
      return;
    }
    setSigningOut(true);
    setError('');
    try {
      await logout();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setSigningOut(false);
    }
  }
  const detail = pathname.startsWith('/jobs/');
  const current = detail
    ? '任务详情'
    : navigation.find((item) => pathname === item.to)?.label || '页面不存在';

  return (
    <div className="min-h-svh">
      <a
        href="#main-content"
        className="sr-only rounded-md bg-primary px-3 py-2 text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[70]"
      >
        跳到主要内容
      </a>
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 md:px-6">
          <Brand />
          <nav
            aria-label="主导航"
            className="order-3 flex w-full items-center gap-1 overflow-x-auto md:order-2 md:w-auto md:flex-1"
          >
            {navigation
              .filter((item) => user.is_admin || !item.to.startsWith('/admin'))
              .map(({ to, icon: Icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    `flex h-[34px] shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${isActive ? 'bg-accent text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`
                  }
                >
                  <Icon
                    aria-hidden="true"
                    className="size-4"
                  />
                  {label}
                </NavLink>
              ))}
          </nav>
          <div className="order-2 ml-auto md:order-3">
            <AccountMenu
              user={user}
              signingOut={signingOut}
              onLogout={() => void signOut()}
            />
          </div>
        </div>
      </header>
      <main
        id="main-content"
        tabIndex={-1}
        className="mx-auto min-w-0 max-w-7xl px-4 py-5 outline-none md:px-6"
      >
        <nav
          aria-label="面包屑"
          className="mb-5 flex items-center gap-2 text-xs leading-4 text-muted-foreground"
        >
          <span>工作台</span>
          <ChevronRight
            aria-hidden="true"
            className="size-3"
          />
          {detail && (
            <>
              <Link
                to="/jobs"
                className="hover:text-foreground"
              >
                签名历史
              </Link>
              <ChevronRight
                aria-hidden="true"
                className="size-3"
              />
            </>
          )}
          <span
            aria-current="page"
            className="font-medium text-foreground"
          >
            {current}
          </span>
        </nav>
        <ErrorBox message={error} />
        <Outlet />
      </main>
    </div>
  );
}
