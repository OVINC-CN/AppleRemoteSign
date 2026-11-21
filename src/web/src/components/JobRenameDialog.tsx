import { Pencil } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ErrorBox } from '@/components/shared';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiError, api, errorMessage } from '@/lib/api';
import type { Job } from '@/lib/types';

export function JobRenameDialog({
  job,
  disabled,
  onOpenChange,
  onRenamed,
}: {
  job: Job;
  disabled: boolean;
  onOpenChange: (open: boolean) => void;
  onRenamed: (job: Job) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(job.display_name);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  function changeOpen(value: boolean) {
    if (request.current) {
      return;
    }
    if (value) {
      setName(job.display_name);
      setError('');
    }
    setOpen(value);
    onOpenChange(value);
  }

  async function save() {
    if (request.current || !name.trim()) {
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setError('');
    try {
      const value = await api<Job>(`/api/v1/jobs/${job.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: name.trim(),
        }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) {
        return;
      }
      onRenamed(value);
      setOpen(false);
      onOpenChange(false);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setError(
        error instanceof ApiError && error.status === 409
          ? '该任务正在处理或刷新，请稍后重命名。'
          : errorMessage(error),
      );
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setSaving(false);
      }
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={changeOpen}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          disabled={disabled}
        >
          <Pencil className="size-4" />
          重命名
        </Button>
      </DialogTrigger>
      <DialogContent showCloseButton={!saving}>
        <DialogHeader>
          <DialogTitle>重命名任务</DialogTitle>
          <DialogDescription>
            修改任务的显示名称，原始文件和下载文件的名称保持不变。
          </DialogDescription>
        </DialogHeader>
        <form
          noValidate
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="job-display-name">任务名称</Label>
            <Input
              id="job-display-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={255}
              required
              disabled={saving}
            />
          </div>
          <ErrorBox message={error} />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={() => changeOpen(false)}
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={!name.trim()}
              loading={saving}
            >
              保存名称
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
