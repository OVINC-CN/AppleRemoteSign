package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func configFilename(arguments []string, directory string) (string, error) {
	// 提前读取配置路径，使 init 阶段和 Cobra 命令使用同一份文件。
	var filename string
	option := "--" + ConfigFlagName
	for index := 0; index < len(arguments); index++ {
		argument := arguments[index]
		if argument == "--" {
			break
		}
		switch {
		case argument == option:
			index++
			if index == len(arguments) || arguments[index] == "" ||
				strings.HasPrefix(arguments[index], "--") {
				return "", errors.New("--config requires a file path")
			}
			filename = arguments[index]
		case strings.HasPrefix(argument, option+"="):
			filename = strings.TrimPrefix(argument, option+"=")
			if filename == "" {
				return "", errors.New("--config requires a file path")
			}
		}
	}
	if filename != "" {
		if !filepath.IsAbs(filename) {
			filename = filepath.Join(directory, filename)
		}
		return filepath.Clean(filename), nil
	}

	// 逐级查找配置，兼容从 API 目录启动和 Go 在包目录执行检查。
	for {
		candidate := filepath.Join(directory, defaultConfigFilename)
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		} else if !errors.Is(err, os.ErrNotExist) {
			return "", fmt.Errorf("config file lookup failed: %w", err)
		}
		parent := filepath.Dir(directory)
		if parent == directory {
			return "", errors.New("config file not found; use --config to specify a file")
		}
		directory = parent
	}
}
