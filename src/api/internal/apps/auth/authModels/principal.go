package authModels

type Principal struct {
	UserID, Username, KeyID, ApplicationID, SessionHash, CSRFToken string
	Admin                                                          bool
}
