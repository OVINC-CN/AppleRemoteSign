package cos

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"sync"

	sdk "github.com/tencentyun/cos-go-sdk-v5"
	"golang.org/x/sync/errgroup"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/cdn"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

const (
	multipartThreshold = 16 << 20
	minimumPartSize    = 8 << 20
	maximumParts       = 10_000
	maximumKeyBytes    = 850
	uploadConcurrency  = 4
)

var client *sdk.Client
var mu sync.Mutex

func get() (*sdk.Client, error) {
	mu.Lock()
	defer mu.Unlock()
	if client != nil {
		return client, nil
	}

	cfg := config.Config.COS
	bucketURL, err := url.Parse(cfg.BucketURL)
	if err != nil || cfg.SecretID == "" || cfg.SecretKey == "" {
		return nil, errors.New("COS configuration failed.")
	}
	baseURL := &sdk.BaseURL{BucketURL: bucketURL}
	if !baseURL.Check() {
		return nil, errors.New("COS configuration failed.")
	}
	transport := http.DefaultTransport
	if base, ok := transport.(*http.Transport); ok {
		cloned := base.Clone()
		cloned.DialContext = (&net.Dialer{Timeout: cfg.ConnectTimeout}).DialContext
		cloned.ResponseHeaderTimeout = cfg.ReadTimeout
		transport = cloned
	}
	client = sdk.NewClient(baseURL, &http.Client{
		Transport: &sdk.AuthorizationTransport{
			SecretID:     cfg.SecretID,
			SecretKey:    cfg.SecretKey,
			SessionToken: cfg.SessionToken,
			Transport:    transport,
		},
	})
	return client, nil
}

func Exists(ctx context.Context, key string) (bool, error) {
	c, err := get()
	if err != nil {
		return false, err
	}
	exists, err := c.Object.IsExist(ctx, key)
	if err != nil {
		return false, errors.New("COS artifact lookup failed.")
	}
	return exists, nil
}

func Upload(ctx context.Context, file *os.File, key, name string) error {
	if len(key) > maximumKeyBytes {
		return errors.New("COS artifact key exceeds 850 UTF-8 bytes.")
	}
	c, err := get()
	if err != nil {
		return err
	}
	stat, err := file.Stat()
	if err != nil {
		return errors.New("COS artifact upload failed.")
	}
	headers := &sdk.ObjectPutHeaderOptions{
		ContentType:        "application/zip",
		ContentDisposition: "attachment; filename*=UTF-8''" + cdn.Escape(name),
	}
	if stat.Size() < multipartThreshold {
		_, err = c.Object.Put(ctx, key, file, &sdk.ObjectPutOptions{
			ObjectPutHeaderOptions: headers,
		})
	} else {
		err = uploadParts(ctx, c.Object, file, stat.Size(), key, headers)
	}
	if err != nil {
		return errors.New("COS artifact upload failed.")
	}
	return nil
}

func uploadParts(
	ctx context.Context,
	objects *sdk.ObjectService,
	file *os.File,
	size int64,
	key string,
	headers *sdk.ObjectPutHeaderOptions,
) (err error) {
	// SDK 的高阶 Upload 在合并分块时使用独立的上下文。逐个调用 SDK 接口以保留租约取消。
	upload, _, err := objects.InitiateMultipartUpload(ctx, key, &sdk.InitiateMultipartUploadOptions{
		ObjectPutHeaderOptions: headers,
	})
	if err != nil {
		return err
	}
	if upload.UploadID == "" {
		return errors.New("COS multipart upload ID is missing.")
	}
	defer func() {
		if err != nil && ctx.Err() == nil {
			_, _ = objects.AbortMultipartUpload(ctx, key, upload.UploadID)
		}
	}()

	partSize := max(minimumPartSize, (size-1)/maximumParts+1)
	parts := make([]sdk.Object, (size-1)/partSize+1)
	group, partCtx := errgroup.WithContext(ctx)
	group.SetLimit(uploadConcurrency)
	for i := range parts {
		if partCtx.Err() != nil {
			break
		}
		group.Go(func() error {
			if err := partCtx.Err(); err != nil {
				return err
			}
			offset := int64(i) * partSize
			length := min(partSize, size-offset)
			part, err := objects.UploadPart(
				partCtx, key, upload.UploadID, i+1,
				io.NewSectionReader(file, offset, length),
				&sdk.ObjectUploadPartOptions{ContentLength: length},
			)
			if err != nil {
				return err
			}
			etag := part.Header.Get("ETag")
			if etag == "" {
				return errors.New("COS upload part ETag is missing.")
			}
			parts[i] = sdk.Object{PartNumber: i + 1, ETag: etag}
			return nil
		})
	}
	if err = group.Wait(); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	_, _, err = objects.CompleteMultipartUpload(ctx, key, upload.UploadID,
		&sdk.CompleteMultipartUploadOptions{Parts: parts},
	)
	return err
}
