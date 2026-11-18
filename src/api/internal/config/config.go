package config

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/goccy/go-yaml"
	"github.com/sirupsen/logrus"
)

// Config 保存进程共享的配置，其他模块直接读取。
var Config *config

// FilePath 保存启动时实际读取的配置文件路径。
var FilePath string

func init() {
	// 确定配置文件，保证数据库等依赖包初始化前已完成读取。
	workingDirectory, err := os.Getwd()
	if err != nil {
		logrus.Fatalf("Working directory lookup failed: %v", err)
	}
	FilePath, err = configFilename(os.Args[1:], workingDirectory)
	if err != nil {
		logrus.Fatalf("Config path lookup failed: %v", err)
	}

	// 加载并校验配置，不读取环境变量或 .env 文件。
	Config, err = load(FilePath)
	if err != nil {
		logrus.Fatalf("Config initialization failed: %v", err)
	}

}

func load(filename string) (*config, error) {
	// 读取指定的本地文件，文件不存在时直接报错。
	data, err := os.ReadFile(filename) // #nosec G304 -- 路径来自本地启动参数，不接受 HTTP 请求输入。
	if err != nil {
		return nil, fmt.Errorf("config file read failed: %w", err)
	}

	// 严格解析单个 YAML 文档，拒绝未知字段和重复键。
	cfg := &config{}
	decoder := yaml.NewDecoder(bytes.NewReader(data), yaml.Strict())
	if err := decoder.Decode(cfg); err != nil {
		return nil, errors.New("invalid YAML configuration; check fields and value types")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("config must contain one YAML document")
	}

	// 检查运行参数，避免带着无效配置初始化共享资源。
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}
