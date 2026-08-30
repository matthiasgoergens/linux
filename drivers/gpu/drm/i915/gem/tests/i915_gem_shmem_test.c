// SPDX-License-Identifier: MIT

#include <kunit/test.h>
#include <kunit/static_stub.h>

#include <linux/pagemap.h>
#include <linux/shmem_fs.h>
#include <linux/writeback.h>

int i915_shmem_write_folio(struct folio *folio);
int i915_shmem_writeback_folio(struct writeback_control *wbc,
				struct folio *folio);

static bool activated;

static int activate_folio(struct folio *folio)
{
	activated = true;
	return AOP_WRITEPAGE_ACTIVATE;
}

static void i915_shmem_writeback_unlocks_activated_folio(struct kunit *test)
{
	struct folio *folio;
	struct file *file;
	loff_t pos = 0;
	u32 data = 0xdeadbeef;
	struct writeback_control wbc = {};
	bool unlocked;

	file = shmem_file_setup("i915-kunit", PAGE_SIZE, EMPTY_VMA_FLAGS);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, file);

	KUNIT_ASSERT_EQ(test, kernel_write(file, &data, sizeof(data), &pos),
			(long)sizeof(data));

	folio = filemap_get_folio(file->f_mapping, 0);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, folio);
	kunit_activate_static_stub(test, i915_shmem_write_folio, activate_folio);
	activated = false;

	folio_lock(folio);
	/* The writepage callback asks the caller to activate the locked folio. */
	KUNIT_EXPECT_EQ(test, i915_shmem_writeback_folio(&wbc, folio), 0);
	KUNIT_ASSERT_TRUE(test, activated);

	unlocked = folio_trylock(folio);
	KUNIT_EXPECT_TRUE(test, unlocked);
	/* Also recover the deliberately exposed lock leak on an unfixed kernel. */
	folio_unlock(folio);
	folio_put(folio);
	fput(file);
}

static struct kunit_case i915_gem_shmem_test_cases[] = {
	KUNIT_CASE(i915_shmem_writeback_unlocks_activated_folio),
	{}
};

static struct kunit_suite i915_gem_shmem_test_suite = {
	.name = "i915_gem_shmem",
	.test_cases = i915_gem_shmem_test_cases,
};

kunit_test_suite(i915_gem_shmem_test_suite);

MODULE_LICENSE("GPL");
