//go:build testfixture

package cos

// ResetFixture 让每组临时凭据和内存 HTTP 传输使用独立的 SDK 客户端。
func ResetFixture() { mu.Lock(); defer mu.Unlock(); client = nil }
