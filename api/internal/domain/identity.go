package domain

// Principal es la identidad verificada que llega en el token. (Issuer, Subject) es la clave
// estable del usuario: `sub` solo es único dentro de su emisor.
type Principal struct {
	Issuer  string
	Subject string
	Email   string
}
