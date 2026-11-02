using System;

namespace PeekSetup.Services
{
    public interface IAuthorizationStore
    {
        string[] GetAuthorizedIdentities(string userSid);
        bool SetAuthorizedIdentities(string userSid, string[] profileIds);
        bool AddAuthorizedIdentity(string userSid, string profileId);
        bool RemoveAuthorizedIdentity(string userSid, string profileId);
        bool DeleteIdentitiesKey(string userSid);
        bool HasAuthorizedIdentities(string userSid);
    }
}
