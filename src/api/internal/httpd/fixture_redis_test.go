//go:build testfixture

package httpd

import (
	"context"
	"errors"
	"fmt"
	"net"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

type entry struct {
	token   string
	expires time.Time
}
type memoryRedis struct {
	mu      sync.Mutex
	leases  map[string]entry
	failure error
}

func (r *memoryRedis) DialHook(redis.DialHook) redis.DialHook {
	return func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("unexpected network access")
	}
}

func (r *memoryRedis) ProcessPipelineHook(redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(context.Context, []redis.Cmder) error { return errors.New("unexpected pipeline") }
}

func (r *memoryRedis) ProcessHook(redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		r.mu.Lock()
		defer r.mu.Unlock()
		if r.failure != nil {
			return r.failure
		}
		a := cmd.Args()
		switch cmd.Name() {
		case "ping":
			cmd.(*redis.StatusCmd).SetVal("PONG")
		case "set":
			key := a[1].(string)
			v := r.leases[key]
			ok := v.expires.Before(time.Now())
			if ok {
				r.leases[key] = entry{a[2].(string), time.Now().Add(time.Minute)}
			}
			cmd.(*redis.BoolCmd).SetVal(ok)
		case "get":
			v := r.leases[a[1].(string)]
			if v.expires.Before(time.Now()) {
				return redis.Nil
			}
			cmd.(*redis.StringCmd).SetVal(v.token)
		case "evalsha", "eval":
			key := a[3].(string)
			v := r.leases[key]
			ok := int64(0)
			if v.token == a[4] && v.expires.After(time.Now()) {
				ok = 1
				if len(a) > 5 {
					v.expires = time.Now().Add(time.Minute)
					r.leases[key] = v
				} else {
					delete(r.leases, key)
				}
			}
			cmd.(*redis.Cmd).SetVal(ok)
		default:
			return fmt.Errorf("unexpected Redis command %s", cmd.Name())
		}
		return nil
	}
}
