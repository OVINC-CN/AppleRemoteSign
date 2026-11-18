package db

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"github.com/OVINC-CN/AppleSign/src/api/internal/logger"
)

const leaseTTL = 60 * time.Second

var ErrLease = errors.New(
	"Redis coordination is unavailable or the task lease was lost. Query the existing task before attempting another upload.",
)

var renew = redis.NewScript(
	"if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('pexpire',KEYS[1],ARGV[2]) end return 0",
)

var release = redis.NewScript(
	"if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) end return 0",
)

type leaseKey struct{}
type Lease struct {
	ctx        context.Context
	cancel     context.CancelCauseFunc
	key, token string
	done       chan struct{}
	mu         sync.RWMutex
	deadline   time.Time
}

func (l *Lease) Context() context.Context { return l.ctx }
func Acquire(ctx context.Context, resource string, wait bool) (*Lease, error) {
	l := &Lease{
		key:   "apple-remote-sign:lock:" + resource,
		token: uuid.NewString(),
		done:  make(chan struct{}),
	}
	for {
		if err := Check(ctx); err != nil {
			return nil, err
		}
		started := time.Now()
		ok, err := RDB().SetNX(ctx, l.key, l.token, leaseTTL).Result()
		if err != nil {
			return nil, ErrLease
		}
		if ok {
			l.deadline = started.Add(leaseTTL)
			break
		}
		if !wait {
			return nil, nil
		}
		select {
		case <-ctx.Done():
			return nil, context.Cause(ctx)
		case <-time.After(100 * time.Millisecond):
		}
	}
	base, cancel := context.WithCancelCause(ctx)
	l.cancel = cancel
	l.ctx = context.WithValue(base, leaseKey{}, l)
	go l.maintain()
	return l, nil
}

func (l *Lease) maintain() {
	defer close(l.done)
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-l.ctx.Done():
			return
		case <-ticker.C:
			started := time.Now()
			ok, err := renew.Run(l.ctx, RDB(), []string{l.key}, l.token, leaseTTL.Milliseconds()).
				Int()
			l.mu.Lock()
			valid := started.Before(l.deadline) && time.Now().Before(l.deadline)
			if ok == 1 && err == nil && valid {
				l.deadline = started.Add(leaseTTL)
			}
			l.mu.Unlock()
			if ok != 1 || err != nil || !valid {
				l.cancel(ErrLease)
				return
			}
		}
	}
}

func Check(ctx context.Context) error {
	if err := context.Cause(ctx); err != nil {
		return err
	}
	l, _ := ctx.Value(leaseKey{}).(*Lease)
	if l == nil {
		return nil
	}
	token, e := RDB().Get(ctx, l.key).Result()
	l.mu.RLock()
	valid := time.Now().Before(l.deadline)
	l.mu.RUnlock()
	if !valid || e != nil || token != l.token {
		l.cancel(ErrLease)
		return ErrLease
	}
	return nil
}

func (l *Lease) Close() error {
	l.cancel(context.Canceled)
	<-l.done
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	ok, e := release.Run(ctx, RDB(), []string{l.key}, l.token).Int()
	if e != nil || ok != 1 {
		logger.Logger.Errorf("redis.release_failed: %T: %v", e, e)
		return ErrLease
	}
	return nil
}
