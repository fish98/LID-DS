import sys
import random
import urllib.request

from lid_ds.core import Scenario
from lid_ds.core.collector.json_file_store import JSONFileStorage
from lid_ds.core.image import StdinCommand, Image
from lid_ds.sim import gen_schedule_wait_times, Sampler
from lid_ds.utils.docker_utils import get_ip_address

class PHP_CWE_434(Scenario):

    def init_victim(self, container, logger):
        pass

    def wait_for_availability(self, container):
        try:
            victim_ip = get_ip_address(container)
            url = "http://" + victim_ip + "/login.php"
            print("checking... is victim ready?")
            with urllib.request.urlopen(url) as response:
                data = response.read().decode("utf8")
                if "Login :: Damn Vulnerable Web Application" in data:
                    print("is ready...")
                    return True
                else:
                    print("not ready yet...")
                    return False
        except Exception as error:
            print("not ready yet with error: " + str(error))
            return False


if __name__ == '__main__':
    warmup_time = int(sys.argv[1])
    recording_time = int(sys.argv[2])
    do_exploit = int(sys.argv[3])
    if do_exploit < 1:
        exploit_time = 0
    else:
        exploit_time = random.randint(int(recording_time * .3),
                                      int(recording_time * .8)) if recording_time != -1 else random.randint(5, 15)
    min_user_count = 10
    max_user_count = 25
    user_count = random.randint(min_user_count, max_user_count)

    if recording_time == -1:
        # 1800s = 5hrs -> normal behaviour needs to be generated for a long time until exploit ends
        wait_times = Sampler("Aug28").ip_timerange_sampling(user_count, 1800)
    else:
        wait_times = [gen_schedule_wait_times(recording_time) for _ in range(user_count)]
    storage_services = [JSONFileStorage()]

    victim = Image("victim_php")
    normal = Image("normal_php", command=StdinCommand(""), init_args="-ip ${victim}")
    exploit = Image("exploit_php", command=StdinCommand(""), init_args="-ip ${victim}")

    php_scenario = PHP_CWE_434(
        victim=victim,
        normal=normal,
        exploit=exploit,
        wait_times=wait_times,
        warmup_time=warmup_time,
        recording_time=recording_time,
        storage_services=storage_services,
        exploit_start_time=exploit_time
    )
    php_scenario()
