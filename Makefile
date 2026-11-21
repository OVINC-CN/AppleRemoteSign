CDN_WEB_DIST ?= src/web/dist
export CDN_ORIGIN CDN_WEB_DIST

.PHONY: cdn-urls

cdn-urls:
	@node src/web/scripts/cdn-urls.ts "$$CDN_ORIGIN" "$$CDN_WEB_DIST"
