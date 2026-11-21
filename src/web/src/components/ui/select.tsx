import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import { Select as SelectPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';

type Option = {
  value: string;
  label: string;
};

function Select({
  options,
  value,
  onValueChange,
  ...props
}: Pick<
  ComponentProps<typeof SelectPrimitive.Trigger>,
  'id' | 'aria-label' | 'disabled'
> & {
  options: Option[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const selected = options.find((option) => option.value === value);
  const current = selected ?? options[0];
  return (
    <SelectPrimitive.Root
      disabled={props.disabled}
      value={`option:${value}`}
      onValueChange={(option) => onValueChange(option.slice('option:'.length))}
    >
      <SelectPrimitive.Trigger
        {...props}
        className="flex h-8 w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-card px-2.5 text-left text-[13px] outline-none transition-colors hover:bg-muted focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 disabled:opacity-50"
      >
        <span className="min-w-0 flex-1 truncate">
          <SelectPrimitive.Value>{current?.label}</SelectPrimitive.Value>
        </span>
        <SelectPrimitive.Icon asChild>
          <ChevronDown
            aria-hidden="true"
            className="size-3.5 shrink-0 text-muted-foreground"
          />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          align="start"
          sideOffset={4}
          className="z-50 max-h-(--radix-select-content-available-height) min-w-(--radix-select-trigger-width) max-w-(--radix-select-content-available-width) overflow-hidden rounded-md border bg-popover text-[13px] text-popover-foreground shadow-md"
        >
          <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center text-muted-foreground">
            <ChevronUp className="size-3.5" />
          </SelectPrimitive.ScrollUpButton>
          <SelectPrimitive.Viewport className="p-1">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={`option:${option.value}`}
                className="relative flex min-h-8 cursor-pointer items-center rounded-sm px-2 py-1.5 pr-7 leading-5 outline-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
              >
                <SelectPrimitive.ItemText className="min-w-0 wrap-anywhere">
                  {option.label}
                </SelectPrimitive.ItemText>
                <SelectPrimitive.ItemIndicator className="absolute right-2">
                  <Check
                    aria-hidden="true"
                    className="size-3.5"
                  />
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
          <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center text-muted-foreground">
            <ChevronDown className="size-3.5" />
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export { Select };
