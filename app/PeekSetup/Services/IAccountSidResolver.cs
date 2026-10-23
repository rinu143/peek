namespace PeekSetup.Services
{
    public interface IAccountSidResolver
    {
        bool ValidateUserSid(string username, string expectedSid);
        string? ResolveUserToSid(string username);
    }
}
