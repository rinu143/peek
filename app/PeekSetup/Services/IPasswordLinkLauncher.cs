using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public interface IPasswordLinkLauncher
    {
        Task<int> LaunchLinkPasswordAsync(string username, string sid, string profileId);
        Task<int> LaunchAuthorizeIdentityAsync(string sid, string profileId, string? username = null);
        Task<int> LaunchRevokeIdentityAsync(string sid, string profileId);
        Task<int> LaunchUnlinkAsync(string sid);
    }
}
