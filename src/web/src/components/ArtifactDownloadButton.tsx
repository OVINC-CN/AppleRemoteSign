import { ArrowDownToLine } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip } from '@/components/ui/tooltip';
import { ApiError, api, errorMessage } from '@/lib/api';

export function ArtifactDownloadButton({
  jobId,
  label = '下载产物',
  compact = false,
  onError,
}: {
  jobId: string;
  label?: string;
  compact?: boolean;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [readyUrl, setReadyUrl] = useState('');
  const pending = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      pending.current?.abort();
      pending.current = null;
    },
    [],
  );

  async function download() {
    if (pending.current) {
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setReadyUrl('');
    onError('');
    try {
      const { url } = await api<{
        url: string;
      }>(`/api/v1/jobs/${jobId}/artifact-url`, {
        signal: controller.signal,
        cache: 'no-store',
        redirect: 'error',
      });
      if (controller.signal.aborted) {
        return;
      }
      const target = new URL(url);
      if (
        ![
          'http:',
          'https:',
        ].includes(target.protocol)
      ) {
        throw new Error('服务返回了无效的下载地址，请稍后重试。');
      }
      const tab = window.open(target.href, '_blank');
      if (tab) {
        tab.opener = null;
      } else {
        setReadyUrl(target.href);
        onError('下载地址已就绪，浏览器拦截了新标签页。请点击“打开下载”。');
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        onError(
          error instanceof ApiError && error.status === 410
            ? '文件已过期或已被删除，请重新获取制品。'
            : errorMessage(error),
        );
      }
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <>
      <Tooltip text="在新标签页下载">
        <Button
          aria-label={label}
          loading={busy}
          icon={ArrowDownToLine}
          iconClassName={compact ? 'size-3.5' : 'size-4'}
          size={compact ? 'icon-sm' : 'default'}
          variant={compact ? 'ghost' : 'default'}
          onClick={() => void download()}
        >
          {!compact && label}
        </Button>
      </Tooltip>
      {readyUrl && (
        <Button
          asChild
          size="sm"
          variant="link"
        >
          <a
            href={readyUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => onError('')}
          >
            打开下载
          </a>
        </Button>
      )}
    </>
  );
}
