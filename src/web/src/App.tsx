import { ArrowUpRight, KeyRound } from 'lucide-react';
import {
  Link,
  Navigate,
  Route,
  Routes,
  useSearchParams,
} from 'react-router-dom';
import { ErrorBox, Loading } from '@/components/shared';
import { Button } from '@/components/ui/button';
import { Brand, WorkspaceLayout } from '@/components/workspace';
import { apiUrl, errorMessage } from '@/lib/api';
import { useSession } from '@/lib/session';
import { AdminPage } from '@/pages/Admin';
import { HistoryPage } from '@/pages/History';
import { JobPage } from '@/pages/Job';
import { UploadPage } from '@/pages/Upload';

function Login() {
  const { user, error, reload } = useSession();
  const [params] = useSearchParams();
  if (user) {
    return (
      <Navigate
        to="/jobs"
        replace
      />
    );
  }
  const authErrors: Record<string, string> = {
    access_denied: '此账号不在登录白名单内，或账号身份发生变化。请联系管理员。',
    login_cookie_missing:
      '浏览器未返回登录校验 Cookie。请重新登录；若仍失败，请联系管理员检查 API 地址和 Cookie 配置。',
    login_cookie_mismatch: '登录校验已失效，请从此页面重新登录。',
    login_expired: '本次登录已过期或已使用，请重新登录。',
  };
  const authError = params.has('error')
    ? Object.hasOwn(authErrors, params.get('error') || '')
      ? authErrors[params.get('error') || '']
      : '登录未完成，请重新登录。'
    : '';
  let href = '';
  let configurationError = '';
  try {
    href = apiUrl('/api/v1/auth/login');
  } catch (error) {
    configurationError = errorMessage(error);
  }
  return (
    <main className="flex min-h-svh flex-col">
      <header className="p-5 md:p-6">
        <Brand />
      </header>
      <div className="m-auto w-full max-w-md px-5 py-12">
        <section className="rounded-xl border bg-card p-6 md:p-8">
          <KeyRound className="mb-5 size-6 text-primary" />
          <h1 className="text-2xl font-semibold leading-8 tracking-tight">
            登录工作台
          </h1>
          <p className="mb-6 mt-2 text-[13px] leading-5 text-muted-foreground">
            使用组织账号安全登录。可访问的应用由管理员授权。
          </p>
          <ErrorBox message={configurationError || error || authError} />
          {href && (
            <Button
              asChild
              className="w-full"
            >
              <a href={href}>
                使用 OIDC 登录
                <ArrowUpRight className="size-4" />
              </a>
            </Button>
          )}
          {error && (
            <Button
              variant="ghost"
              className="mt-3 w-full"
              onClick={() => void reload()}
            >
              重新检查连接
            </Button>
          )}
          <p className="mt-6 text-center text-xs text-muted-foreground">
            没有应用权限？请联系你的管理员。
          </p>
        </section>
      </div>
    </main>
  );
}
function AdminGuard() {
  return useSession().user?.is_admin ? (
    <AdminPage />
  ) : (
    <Navigate
      to="/jobs"
      replace
    />
  );
}
export default function App() {
  const { loading } = useSession();
  if (loading) {
    return <Loading />;
  }
  return (
    <Routes>
      <Route
        path="/login"
        element={<Login />}
      />
      <Route element={<WorkspaceLayout />}>
        <Route
          path="/jobs"
          element={<HistoryPage />}
        />
        <Route
          path="/jobs/:jobId"
          element={<JobPage />}
        />
        <Route
          path="/upload"
          element={<UploadPage />}
        />
        <Route
          path="/admin/applications"
          element={<AdminGuard />}
        />
        <Route
          path="/"
          element={
            <Navigate
              to="/jobs"
              replace
            />
          }
        />
        <Route
          path="*"
          element={
            <div className="px-4 py-10 text-center">
              <h1 className="mb-4 text-2xl font-semibold leading-8">
                页面不存在
              </h1>
              <Button asChild>
                <Link to="/jobs">返回签名历史</Link>
              </Button>
            </div>
          }
        />
      </Route>
    </Routes>
  );
}
