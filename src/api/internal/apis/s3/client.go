package s3

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"sync"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/feature/s3/transfermanager"
	sdk "github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/smithy-go"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apis/cdn"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

var client *sdk.Client
var mu sync.Mutex

func get(ctx context.Context) (*sdk.Client, error) {
	mu.Lock()
	defer mu.Unlock()
	if client != nil {
		return client, nil
	}
	c := config.Config.S3
	transport := http.DefaultTransport
	if base, ok := transport.(*http.Transport); ok {
		cloned := base.Clone()
		cloned.DialContext = (&net.Dialer{Timeout: c.ConnectTimeout}).DialContext
		cloned.ResponseHeaderTimeout = c.ReadTimeout
		transport = cloned
	}
	opts := []func(*awsconfig.LoadOptions) error{
		awsconfig.WithRegion(c.Region),
		awsconfig.WithHTTPClient(&http.Client{Transport: transport}),
		awsconfig.WithRetryMaxAttempts(3),
	}
	if c.AccessKeyID != "" {
		opts = append(
			opts,
			awsconfig.WithCredentialsProvider(
				credentials.NewStaticCredentialsProvider(
					c.AccessKeyID,
					c.SecretAccessKey,
					c.SessionToken,
				),
			),
		)
	}
	cfg, e := awsconfig.LoadDefaultConfig(ctx, opts...)
	if e != nil {
		return nil, errors.New("S3 configuration failed.")
	}
	client = sdk.NewFromConfig(cfg, func(o *sdk.Options) {
		if c.EndpointURL != "" {
			o.BaseEndpoint = aws.String(c.EndpointURL)
		}
		o.UsePathStyle = c.AddressingStyle == "path" ||
			c.AddressingStyle == "auto" && c.EndpointURL != ""
		o.RequestChecksumCalculation = aws.RequestChecksumCalculationWhenRequired
		o.ResponseChecksumValidation = aws.ResponseChecksumValidationWhenRequired
	})
	return client, nil
}

func Exists(ctx context.Context, key string) (bool, error) {
	c, e := get(ctx)
	if e != nil {
		return false, e
	}
	_, e = c.HeadObject(ctx, &sdk.HeadObjectInput{Bucket: &config.Config.S3.Bucket, Key: &key})
	if e != nil {
		var a smithy.APIError
		if errors.As(e, &a) &&
			(a.ErrorCode() == "NotFound" || a.ErrorCode() == "NoSuchKey" || a.ErrorCode() == "404") {
			return false, nil
		}
		return false, errors.New("S3 artifact lookup failed.")
	}
	return true, nil
}

func Upload(ctx context.Context, f *os.File, key, name, sha string) error {
	c, e := get(ctx)
	if e != nil {
		return e
	}
	uploader := transfermanager.New(c, func(o *transfermanager.Options) { o.Concurrency = 4 })
	_, e = uploader.UploadObject(
		ctx,
		&transfermanager.UploadObjectInput{
			Bucket:             &config.Config.S3.Bucket,
			Key:                &key,
			Body:               f,
			ContentType:        aws.String("application/zip"),
			ContentDisposition: aws.String("attachment; filename*=UTF-8''" + cdn.Escape(name)),
			Metadata:           map[string]string{"sha256": sha},
		},
	)
	if e != nil {
		return errors.New("S3 artifact upload failed.")
	}
	return nil
}
