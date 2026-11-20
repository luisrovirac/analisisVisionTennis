// Package storage implementa el almacenamiento de objetos sobre la API S3
// (MinIO en desarrollo, Cloudflare R2 en producción: solo cambia el endpoint).
package storage

import (
	"context"
	"fmt"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"tennisvision/api/internal/config"
	"tennisvision/api/internal/domain"
)

// S3Store usa dos clientes: uno interno (red de docker) para operar y otro con el
// endpoint público para firmar URLs que el navegador/móvil puedan alcanzar.
type S3Store struct {
	internal *minio.Client
	public   *minio.Client
	bucket   string
}

func NewS3Store(cfg config.S3Config) (*S3Store, error) {
	internal, err := newClient(cfg.Endpoint, cfg.UseSSL, cfg)
	if err != nil {
		return nil, fmt.Errorf("cliente S3 interno: %w", err)
	}
	public, err := newClient(cfg.PublicEndpoint, cfg.PublicUseSSL, cfg)
	if err != nil {
		return nil, fmt.Errorf("cliente S3 público: %w", err)
	}
	return &S3Store{internal: internal, public: public, bucket: cfg.Bucket}, nil
}

// Fijar la región evita que el cliente consulte la ubicación del bucket al firmar.
func newClient(endpoint string, useSSL bool, cfg config.S3Config) (*minio.Client, error) {
	return minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.AccessKey, cfg.SecretKey, ""),
		Secure: useSSL,
		Region: cfg.Region,
	})
}

func (s *S3Store) PresignPut(ctx context.Context, key string, ttl time.Duration) (string, error) {
	u, err := s.public.PresignedPutObject(ctx, s.bucket, key, ttl)
	if err != nil {
		return "", fmt.Errorf("firmar PUT %s: %w", key, err)
	}
	return u.String(), nil
}

func (s *S3Store) Stat(ctx context.Context, key string) (domain.ObjectInfo, error) {
	info, err := s.internal.StatObject(ctx, s.bucket, key, minio.StatObjectOptions{})
	if minio.ToErrorResponse(err).Code == "NoSuchKey" {
		return domain.ObjectInfo{}, domain.ErrObjectNotFound
	}
	if err != nil {
		return domain.ObjectInfo{}, fmt.Errorf("stat %s: %w", key, err)
	}
	return domain.ObjectInfo{Size: info.Size, ContentType: info.ContentType}, nil
}

// S3 responde éxito al borrar una clave inexistente, así que Remove es idempotente.
func (s *S3Store) Remove(ctx context.Context, keys ...string) error {
	for _, key := range keys {
		if err := s.internal.RemoveObject(ctx, s.bucket, key, minio.RemoveObjectOptions{}); err != nil {
			return fmt.Errorf("borrar %s: %w", key, err)
		}
	}
	return nil
}

func (s *S3Store) Ping(ctx context.Context) error {
	ok, err := s.internal.BucketExists(ctx, s.bucket)
	if err != nil {
		return fmt.Errorf("consultar bucket: %w", err)
	}
	if !ok {
		return fmt.Errorf("el bucket %q no existe", s.bucket)
	}
	return nil
}
