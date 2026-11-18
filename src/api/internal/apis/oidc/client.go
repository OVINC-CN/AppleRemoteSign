package oidc

import (
	"context"
	"errors"
	"net/http"
	"slices"
	"strings"
	"unicode/utf8"

	provider "github.com/coreos/go-oidc/v3/oidc"
	"golang.org/x/oauth2"

	"github.com/OVINC-CN/AppleSign/src/api/internal/apps/auth/authUtils"
	"github.com/OVINC-CN/AppleSign/src/api/internal/config"
)

func connect(ctx context.Context) (context.Context, *provider.Provider, oauth2.Config, error) {
	cfg := config.Config.OIDC
	client := &http.Client{
		Timeout:       cfg.Timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	ctx = provider.ClientContext(ctx, client)
	p, err := provider.NewProvider(ctx, cfg.IssuerURL)
	if err != nil {
		return ctx, nil, oauth2.Config{}, err
	}
	// 拒绝只声明对称或无签名 ID token 的提供方。
	var meta struct {
		Algorithms []string `json:"id_token_signing_alg_values_supported"`
	}
	if err := p.Claims(&meta); err != nil {
		return ctx, nil, oauth2.Config{}, err
	}
	allowed := []string{
		"RS256",
		"RS384",
		"RS512",
		"PS256",
		"PS384",
		"PS512",
		"ES256",
		"ES384",
		"ES512",
		"EdDSA",
	}
	if meta.Algorithms != nil &&
		!slices.ContainsFunc(
			meta.Algorithms,
			func(a string) bool { return slices.Contains(allowed, a) },
		) {
		return ctx, nil, oauth2.Config{}, errors.New(
			"provider lacks an asymmetric signing algorithm",
		)
	}
	ep := p.Endpoint()
	ep.AuthStyle = oauth2.AuthStyleInHeader
	if cfg.TokenEndpointAuthMethod == "client_secret_post" {
		ep.AuthStyle = oauth2.AuthStyleInParams
	}
	return ctx, p, oauth2.Config{
		ClientID:     cfg.ClientID,
		ClientSecret: cfg.ClientSecret,
		RedirectURL:  cfg.RedirectURI,
		Scopes:       strings.Fields(cfg.Scopes),
		Endpoint:     ep,
	}, nil
}

type Transaction struct {
	Verifier string `json:"code_verifier"`
	Nonce    string `json:"nonce"`
}

func Authorize(ctx context.Context, state string) (string, Transaction, error) {
	_, _, client, err := connect(ctx)
	if err != nil {
		return "", Transaction{}, err
	}
	t := Transaction{oauth2.GenerateVerifier(), authUtils.Token()}
	return client.AuthCodeURL(
		state,
		oauth2.S256ChallengeOption(t.Verifier),
		provider.Nonce(t.Nonce),
	), t, nil
}

func Identity(ctx context.Context, code string, t Transaction) (string, string, error) {
	ctx, p, client, err := connect(ctx)
	if err != nil {
		return "", "", err
	}
	tok, err := client.Exchange(ctx, code, oauth2.VerifierOption(t.Verifier))
	if err != nil {
		return "", "", err
	}
	raw, _ := tok.Extra("id_token").(string)
	id, err := p.Verifier(&provider.Config{ClientID: client.ClientID}).Verify(ctx, raw)
	if err != nil || t.Nonce == "" || !authUtils.Equal(t.Nonce, id.Nonce) || id.Subject == "" ||
		utf8.RuneCountInString(id.Subject) > 255 {
		return "", "", errors.New("invalid ID token")
	}
	var claims map[string]any
	if err = id.Claims(&claims); err != nil {
		return "", "", err
	}
	field := config.Config.OIDC.UsernameClaim
	if claims[field] == nil {
		info, err := p.UserInfo(ctx, oauth2.StaticTokenSource(tok))
		if err != nil || info.Subject != id.Subject {
			return "", "", errors.New("invalid userinfo subject")
		}
		claims = nil
		if err := info.Claims(&claims); err != nil {
			return "", "", err
		}
	}
	name, ok := claims[field].(string)
	if !ok || name == "" || utf8.RuneCountInString(name) > 255 {
		return "", "", errors.New("missing username claim")
	}
	return name, id.Subject, nil
}
