using System.Threading.Tasks;

namespace PeekSetup.Services
{
    public interface IPasswordLinkLauncher
    {
        Task<int> LaunchLinkPasswordAsync(string username, string sid, string profileId);
        Task<int> LaunchUnlinkAsync(string sid);
    }
}
